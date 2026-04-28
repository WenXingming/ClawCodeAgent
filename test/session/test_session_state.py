"""ISSUE-008 AgentSessionState.from_persisted 单元测试。"""

from __future__ import annotations

import unittest

from session.session_state import AgentSessionState


class SessionStateFromPersistedTests(unittest.TestCase):
    """验证从持久化数据恢复会话运行态的行为。"""

    def test_from_persisted_restores_messages_and_count(self) -> None:
        """基础消息列表与工具调用计数应被完整恢复。"""
        messages = [
            {'role': 'user', 'content': '第一次提问'},
            {'role': 'assistant', 'content': '第一次回答'},
        ]
        transcript = [
            {'role': 'user', 'content': '第一次提问'},
            {'role': 'assistant', 'content': '第一次回答', 'finish_reason': 'stop'},
        ]
        session_state = AgentSessionState.from_persisted(
            messages=messages,
            transcript=transcript,
            tool_call_count=3,
        )

        self.assertEqual(session_state.to_messages(), messages)
        self.assertEqual(session_state.tool_call_count, 3)
        self.assertEqual(len(session_state.transcript()), 2)

    def test_from_persisted_empty_transcript_falls_back_to_messages(self) -> None:
        """历史 transcript 为空时，应从 messages 生成最小可审计条目。"""
        messages = [
            {'role': 'user', 'content': '问题'},
            {'role': 'assistant', 'content': '答案', 'tool_calls': []},
        ]
        session_state = AgentSessionState.from_persisted(
            messages=messages,
            transcript=[],
            tool_call_count=0,
        )

        t = session_state.transcript()
        self.assertEqual(len(t), 2)
        self.assertEqual(t[0]['role'], 'user')
        self.assertEqual(t[0]['content'], '问题')
        self.assertEqual(t[1]['role'], 'assistant')

    def test_from_persisted_preserves_nonempty_transcript(self) -> None:
        """已有 transcript 条目应原样保留，不被 messages 覆盖。"""
        messages = [{'role': 'user', 'content': 'hi'}]
        transcript = [
            {'role': 'user', 'content': 'hi', 'extra_field': 'preserved'},
        ]
        session_state = AgentSessionState.from_persisted(
            messages=messages,
            transcript=transcript,
            tool_call_count=0,
        )

        t = session_state.transcript()
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].get('extra_field'), 'preserved')

    def test_from_persisted_then_append_user_extends_state(self) -> None:
        """恢复后可继续追加新消息，不影响已有状态。"""
        messages = [{'role': 'user', 'content': '旧提问'}]
        session_state = AgentSessionState.from_persisted(
            messages=messages,
            transcript=[{'role': 'user', 'content': '旧提问'}],
            tool_call_count=1,
        )
        session_state.append_user('新提问')

        self.assertEqual(len(session_state.to_messages()), 2)
        self.assertEqual(session_state.to_messages()[-1]['content'], '新提问')
        self.assertEqual(session_state.tool_call_count, 1)  # 追加 user 不增加工具计数

    def test_from_persisted_restores_mcp_materialization_state(self) -> None:
        """恢复会话时应保留 capability shortlist 与已物化句柄。"""
        session_state = AgentSessionState.from_persisted(
            messages=[{'role': 'user', 'content': '旧提问'}],
            transcript=[{'role': 'user', 'content': '旧提问'}],
            tool_call_count=1,
            mcp_capability_shortlist=[
                {
                    'handle': 'mcp:tavily:tavily_search',
                    'tool_name': 'tavily_search',
                    'server_name': 'tavily',
                }
            ],
            materialized_mcp_capability_handles=['mcp:tavily:tavily_search'],
        )

        self.assertEqual(
            session_state.mcp_capability_candidates(),
            ({'handle': 'mcp:tavily:tavily_search', 'tool_name': 'tavily_search', 'server_name': 'tavily'},),
        )
        self.assertEqual(
            session_state.materialized_mcp_capabilities(),
            ('mcp:tavily:tavily_search',),
        )

    def test_update_mcp_capability_window_replaces_previous_window(self) -> None:
        """更新 capability window 时应整体替换旧 shortlist 与句柄列表。"""
        session_state = AgentSessionState.create('初始化')
        session_state.update_mcp_capability_window(
            shortlist=[{'handle': 'old'}],
            materialized_handles=['old'],
        )

        session_state.update_mcp_capability_window(
            shortlist=[{'handle': 'new', 'tool_name': 'search'}],
            materialized_handles=['new'],
        )

        self.assertEqual(
            session_state.mcp_capability_candidates(),
            ({'handle': 'new', 'tool_name': 'search'},),
        )
        self.assertEqual(session_state.materialized_mcp_capabilities(), ('new',))


if __name__ == '__main__':
    unittest.main()
