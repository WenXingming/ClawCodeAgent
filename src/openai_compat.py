"""ISSUE-002 OpenAI-compatible 非流式客户端实现。

这个模块只负责单次 complete 调用，不负责流式 SSE、主循环编排、
预算控制和权限策略。目标是把后端返回的原始 JSON 稳定转换为
`AssistantTurn`，并将底层错误统一封装为可预期的异常类型。

函数的定义顺序大体遵循调用层级，通常是后定义的函数调用前面定义的函数（参见类 `OpenAICompatClient`）。
"""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from .contract_types import (
    AssistantTurn,
    JSONDict,
    ModelConfig,
    OutputSchemaConfig,
    TokenUsage,
    ToolCall,
)


# ---------------------------------------------------------------------------
# 异常定义
# ---------------------------------------------------------------------------


class OpenAICompatError(RuntimeError):
    """OpenAI-compatible 客户端基础异常。"""


class OpenAICompatConnectionError(OpenAICompatError):
    """与模型后端建立连接失败。"""


class OpenAICompatTimeoutError(OpenAICompatError):
    """调用模型后端超时。"""


class OpenAICompatResponseError(OpenAICompatError):
    """模型后端响应格式异常或状态异常。"""


# ---------------------------------------------------------------------------
# 解析辅助函数
# ---------------------------------------------------------------------------


def _join_url(base_url: str, suffix: str) -> str:
    """拼接 base_url 和后缀路径，避免重复斜杠。"""
    base = base_url.rstrip('/')
    return f"{base}/{suffix.lstrip('/')}"


def _normalize_content(content: Any) -> str:
    """把 content 统一转换为字符串。"""
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                # 常见格式是 {'type': 'text', 'text': '...'}。
                if isinstance(item.get('text'), str):
                    parts.append(item['text'])
                    continue
                parts.append(json.dumps(item, ensure_ascii=True))
                continue
            parts.append(str(item))
        return ''.join(parts)
    return str(content)


def _parse_tool_arguments(raw_arguments: Any) -> JSONDict:
    """把工具 arguments 解析为 dict。"""
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if isinstance(raw_arguments, str):
        text = raw_arguments.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpenAICompatResponseError(
                f'Invalid tool arguments payload: {raw_arguments!r}'
            ) from exc
        if not isinstance(parsed, dict):
            raise OpenAICompatResponseError('Tool arguments must decode to JSON object')
        return parsed
    raise OpenAICompatResponseError(
        f'Unsupported tool arguments payload type: {type(raw_arguments).__name__}'
    )


def _build_response_format(
    output_schema: OutputSchemaConfig | None,
) -> JSONDict | None:
    """把 OutputSchemaConfig 转换为 OpenAI-compatible response_format。"""
    if output_schema is None:
        return None
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': output_schema.name,
            'schema': dict(output_schema.schema),
            'strict': output_schema.strict,
        },
    }


def _parse_usage(payload: Any) -> TokenUsage:
    """兼容多种字段命名并转换为 TokenUsage。"""
    if not isinstance(payload, dict):
        return TokenUsage()

    normalized: JSONDict = dict(payload)
    completion_details = payload.get('completion_tokens_details')

    # 兼容 ollama 风格字段。
    if (
        'input_tokens' not in normalized
        and 'prompt_tokens' not in normalized
        and 'prompt_eval_count' in payload
    ):
        normalized['prompt_tokens'] = payload.get('prompt_eval_count')

    if (
        'output_tokens' not in normalized
        and 'completion_tokens' not in normalized
        and 'eval_count' in payload
    ):
        normalized['completion_tokens'] = payload.get('eval_count')

    # 推理 token 可能在 completion_tokens_details 里。
    if (
        'reasoning_tokens' not in normalized
        and isinstance(completion_details, dict)
        and completion_details.get('reasoning_tokens') is not None
    ):
        normalized['reasoning_tokens'] = completion_details.get('reasoning_tokens')

    return TokenUsage.from_dict(normalized)


def _http_error_detail(exc: error.HTTPError) -> str:
    """尽量读取 HTTP 错误体，便于定位后端问题。"""
    try:
        detail = exc.read().decode('utf-8', errors='replace')
    except Exception:
        detail = ''
    return detail or exc.reason or exc.msg or 'unknown error'


# ---------------------------------------------------------------------------
# 客户端实现
# ---------------------------------------------------------------------------


class OpenAICompatClient:
    """最小可运行的 OpenAI-compatible 非流式客户端。"""

    config: ModelConfig  # 客户端固定模型配置。

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def complete(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: OutputSchemaConfig | None = None,
    ) -> AssistantTurn:
        """执行一次非流式模型调用并返回标准化结果。"""
        payload = self._build_payload(
            messages=messages,
            tools=tools,
            output_schema=output_schema,
        )
        response_payload = self._request_json(payload)
        return self._parse_assistant_turn(response_payload)

    def _build_payload(
        self,
        *,
        messages: list[JSONDict],
        tools: list[JSONDict] | None,
        output_schema: OutputSchemaConfig | None,
    ) -> JSONDict:
        """构造发送给 /chat/completions 的请求体。"""
        payload: JSONDict = {
            'model': self.config.model,
            # 只保留 dict，避免脏输入污染请求体。
            'messages': [dict(item) for item in messages if isinstance(item, dict)],
            'temperature': self.config.temperature,
        }

        if tools:
            payload['tools'] = [dict(item) for item in tools if isinstance(item, dict)]
            payload['tool_choice'] = 'auto'

        response_format = _build_response_format(output_schema)
        if response_format is not None:
            payload['response_format'] = response_format
        return payload

    def _build_request(self, payload: JSONDict) -> request.Request:
        """构造标准 POST 请求对象。"""
        return request.Request(
            _join_url(self.config.base_url, '/chat/completions'),
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {self.config.api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )

    def _request_json(self, payload: JSONDict) -> JSONDict:
        """发送请求并返回 JSON 对象。"""
        req = self._build_request(payload)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = _http_error_detail(exc)
            raise OpenAICompatResponseError(
                f'HTTP {exc.code} from model backend: {detail}'
            ) from exc
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OpenAICompatTimeoutError(
                    f'Model request timed out after {self.config.timeout_seconds} seconds'
                ) from exc
            raise OpenAICompatConnectionError(
                f'Unable to reach model backend at {self.config.base_url}: {exc.reason}'
            ) from exc
        except TimeoutError as exc:
            raise OpenAICompatTimeoutError(
                f'Model request timed out after {self.config.timeout_seconds} seconds'
            ) from exc

        try:
            decoded = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError as exc:
            raise OpenAICompatResponseError(
                'Model backend returned invalid JSON'
            ) from exc

        if not isinstance(decoded, dict):
            raise OpenAICompatResponseError(
                'Model backend returned malformed JSON payload'
            )
        return decoded

    def _extract_choice_and_message(self, payload: JSONDict) -> tuple[JSONDict, JSONDict]:
        """提取第一条 choice 及 message，并做结构校验。"""
        choices = payload.get('choices')
        if not isinstance(choices, list) or not choices:
            raise OpenAICompatResponseError('Model backend returned no choices')

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise OpenAICompatResponseError('Model backend returned malformed choice data')

        message = first_choice.get('message')
        if not isinstance(message, dict):
            raise OpenAICompatResponseError('Model backend returned no assistant message')

        return first_choice, message

    @staticmethod
    def _normalize_finish_reason(value: Any) -> str | None:
        """把 finish_reason 统一成 str 或 None。"""
        if value is None:
            return None
        return str(value)

    def _parse_assistant_turn(self, payload: JSONDict) -> AssistantTurn:
        """把后端响应解析为 AssistantTurn。"""
        first_choice, message = self._extract_choice_and_message(payload)
        return AssistantTurn(
            content=_normalize_content(message.get('content')),
            tool_calls=tuple(self._parse_tool_calls_from_message(message)),
            finish_reason=self._normalize_finish_reason(first_choice.get('finish_reason')),
            usage=_parse_usage(payload.get('usage')),
        )

    def _parse_single_tool_call(self, raw_call: JSONDict, index: int) -> ToolCall:
        """解析单个新格式 tool_call。"""
        function_block = raw_call.get('function')
        if not isinstance(function_block, dict):
            raise OpenAICompatResponseError('Malformed tool call function payload')

        name = function_block.get('name')
        if not isinstance(name, str) or not name:
            raise OpenAICompatResponseError('Tool call missing function name')

        call_id = raw_call.get('id')
        if not isinstance(call_id, str) or not call_id:
            call_id = f'call_{index}'

        arguments = _parse_tool_arguments(function_block.get('arguments'))
        return ToolCall(id=call_id, name=name, arguments=arguments)

    def _parse_legacy_function_call(self, function_call: JSONDict) -> ToolCall:
        """解析旧格式 function_call。"""
        name = function_call.get('name')
        if not isinstance(name, str) or not name:
            raise OpenAICompatResponseError('Function call missing name')
        arguments = _parse_tool_arguments(function_call.get('arguments'))
        return ToolCall(id='call_0', name=name, arguments=arguments)

    def _parse_tool_calls_from_message(self, message: JSONDict) -> list[ToolCall]:
        """从 message 中解析工具调用，兼容新旧字段。"""
        raw_tool_calls = message.get('tool_calls')
        if isinstance(raw_tool_calls, list):
            tool_calls: list[ToolCall] = []
            for index, raw_call in enumerate(raw_tool_calls):
                if not isinstance(raw_call, dict):
                    raise OpenAICompatResponseError('Malformed tool call payload')
                tool_calls.append(self._parse_single_tool_call(raw_call, index))
            return tool_calls

        function_call = message.get('function_call')
        if isinstance(function_call, dict):
            return [self._parse_legacy_function_call(function_call)]

        return []
