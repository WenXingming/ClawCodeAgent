"""session 模块公开入口与职责说明。

本模块是 session 领域的对外门面，核心职责如下：
1. 统一暴露 SessionGateway，屏蔽内部存储与恢复实现细节。
2. 为上层提供会话快照保存、会话恢复、运行态状态构建三类能力入口。
3. 约束外部依赖边界：业务代码不得直接导入 session 内部文件。

说明：
- 会话数据契约（AgentSessionSnapshot、AgentSessionState）定义在
	core_contracts.session_contracts，避免内部结构泄漏。
"""

from .session_gateway import SessionGateway

__all__ = ['SessionGateway']
