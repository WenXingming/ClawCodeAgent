"""ISSUE-008 AgentSessionState.from_persisted 单元测试。"""

from __future__ import annotations

import unittest

from src.session.session_state import AgentSessionState


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
        session = AgentSessionState.from_persisted(
            messages=messages,
            transcript=transcript,
            tool_call_count=3,
        )

        self.assertEqual(session.to_messages(), messages)
        self.assertEqual(session.tool_call_count, 3)
        self.assertEqual(len(session.transcript()), 2)

    def test_from_persisted_empty_transcript_falls_back_to_messages(self) -> None:
        """历史 transcript 为空时，应从 messages 生成最小可审计条目。"""
        messages = [
            {'role': 'user', 'content': '问题'},
            {'role': 'assistant', 'content': '答案', 'tool_calls': []},
        ]
        session = AgentSessionState.from_persisted(
            messages=messages,
            transcript=[],
            tool_call_count=0,
        )

        t = session.transcript()
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
        session = AgentSessionState.from_persisted(
            messages=messages,
            transcript=transcript,
            tool_call_count=0,
        )

        t = session.transcript()
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].get('extra_field'), 'preserved')

    def test_from_persisted_then_append_user_extends_state(self) -> None:
        """恢复后可继续追加新消息，不影响已有状态。"""
        messages = [{'role': 'user', 'content': '旧提问'}]
        session = AgentSessionState.from_persisted(
            messages=messages,
            transcript=[{'role': 'user', 'content': '旧提问'}],
            tool_call_count=1,
        )
        session.append_user('新提问')

        self.assertEqual(len(session.to_messages()), 2)
        self.assertEqual(session.to_messages()[-1]['content'], '新提问')
        self.assertEqual(session.tool_call_count, 1)  # 追加 user 不增加工具计数


if __name__ == '__main__':
    unittest.main()
