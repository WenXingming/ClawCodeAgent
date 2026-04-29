"""openai_client 模块公开入口与职责说明。

本模块是 openai_client 领域的对外门面，核心职责如下：
1. 统一暴露 OpenAIClientGateway，屏蔽内部 HTTP 客户端实现细节。
2. 对上层提供模型完成、流式事件、流式聚合三类能力入口。
3. 约束外部依赖边界：业务代码不得直接导入 openai_client 内部文件。

说明：
- 跨模块共享接口与异常契约定义在 core_contracts.openai_contracts。
"""

from .openai_client_gateway import OpenAIClientGateway

__all__ = ['OpenAIClientGateway']
