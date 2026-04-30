"""会话运行态组件。

SessionStateRuntime 是运行态状态创建职责的唯一承载者，整合了从两种来源构建
AgentSessionState 的全部逻辑：
  1. 基于首条用户提示词创建全新的运行态会话
  2. 从持久化的消息与转录数据恢复已有会话状态

设计决策：将 SessionStateFactory 吸收进本类，消除原本过度细粒度的单方法包装类。
"""

from __future__ import annotations

from core_contracts.primitives import JSONDict
from core_contracts.session_contracts import AgentSessionState, SessionValidationError


class SessionStateRuntime:
    """会话运行态状态构建器。

    核心职责：
    - 校验首条提示词并创建全新的 AgentSessionState
    - 将持久化消息与转录数据还原为可继续运行的 AgentSessionState
    """

    # ── 公有接口 ──────────────────────────────────────────────────────────────

    def build_new(self, prompt: str) -> AgentSessionState:
        """基于首条用户提示词创建全新的运行态会话状态。

        Args:
            prompt (str): 用户首条输入文本，不得为空。
        Returns:
            AgentSessionState: 已初始化并写入首条用户消息的状态对象。
        Raises:
            SessionValidationError: prompt 为非字符串或空白字符串时抛出。
        """
        self._validate_prompt(prompt)
        return AgentSessionState.create(prompt)

    def build_from_persisted(
        self,
        messages: tuple[JSONDict, ...],
        transcript: tuple[JSONDict, ...],
    ) -> AgentSessionState:
        """从持久化消息与转录数据恢复运行态会话状态。

        Args:
            messages (tuple[JSONDict, ...]): 持久化的模型上下文消息序列（不可变元组）。
            transcript (tuple[JSONDict, ...]): 持久化的可审计转录序列（不可变元组）。
        Returns:
            AgentSessionState: 恢复完毕、可继续运行的状态对象。
        Raises:
            SessionValidationError: messages 或 transcript 中存在非字典元素时抛出。
        """
        messages_list = self._coerce_dict_sequence(messages, 'messages')
        transcript_list = self._coerce_dict_sequence(transcript, 'transcript')
        return AgentSessionState.from_persisted(messages_list, transcript_list)

    # ── 私有辅助（深度优先：build_new → _validate_prompt；
    #                         build_from_persisted → _coerce_dict_sequence）

    def _validate_prompt(self, prompt: str) -> None:
        """校验初始提示词的类型与内容合法性。

        Args:
            prompt (str): 待校验的提示词文本。
        Returns:
            None
        Raises:
            SessionValidationError: 非字符串或空白字符串时抛出。
        """
        if not isinstance(prompt, str):
            raise SessionValidationError(
                f'prompt 必须为字符串，实际类型为 {type(prompt).__name__}'
            )
        if not prompt.strip():
            raise SessionValidationError('prompt 不能为空白字符串')

    def _coerce_dict_sequence(
        self,
        items: tuple[JSONDict, ...],
        field_name: str,
    ) -> list[JSONDict]:
        """将 JSONDict 元组转为可变字典列表，同时校验元素类型。

        Args:
            items (tuple[JSONDict, ...]): 输入的 JSONDict 不可变序列。
            field_name (str): 字段名称，用于构造错误消息。
        Returns:
            list[JSONDict]: 经过深度复制的字典列表（防止外部数据污染）。
        Raises:
            SessionValidationError: 序列中存在非字典元素时抛出。
        """
        result: list[JSONDict] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise SessionValidationError(
                    f'{field_name}[{index}] 必须为字典，'
                    f'实际类型为 {type(item).__name__}'
                )
            result.append(dict(item))
        return result
