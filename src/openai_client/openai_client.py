"""ISSUE-002/003 OpenAI-compatible 客户端实现。

本模块提供与 OpenAI API 兼容的客户端实现，支持非流式和流式两种调用方式。

主要功能：
- 非流式调用：通过 `complete()` 方法一次性返回完整的 `OneTurnResponse`
- 流式调用：通过 `stream()` 方法流式返回 `StreamEvent` 事件序列
- 流式聚合：提供 `complete_stream()` 方法将流事件聚合回 `OneTurnResponse`

调用方可以根据需求选择：
- "边收边渲染"：使用 `stream()` 实时处理流式事件
- "获取最终结果"：使用 `complete()` 或 `complete_stream()` 获取完整响应
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterator
from urllib import error, request

from core_contracts.config import ModelConfig, OutputSchemaConfig
from core_contracts.protocol import JSONDict, OneTurnResponse, StreamEvent, ToolCall
from core_contracts.usage import TokenUsage


# ---------------------------------------------------------------------------
# 异常定义
# ---------------------------------------------------------------------------


class OpenAIClientError(RuntimeError):
    """OpenAI-compatible 客户端基础异常。"""


class OpenAIConnectionError(OpenAIClientError):
    """与模型后端建立连接失败。"""


class OpenAITimeoutError(OpenAIClientError):
    """调用模型后端超时。"""


class OpenAIResponseError(OpenAIClientError):
    """模型后端响应格式异常或状态异常。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


# ---------------------------------------------------------------------------
# 解析辅助函数
# ---------------------------------------------------------------------------


def _join_url(base_url: str, suffix: str) -> str:
    """拼接 base_url 和后缀路径，避免重复斜杠。"""
    base = base_url.rstrip('/')
    return f"{base}/{suffix.lstrip('/')}"


@dataclass
class _ToolCallBuildState:
    """流式工具调用的中间聚合状态。
    流式返回时，工具调用的参数(arguments)是被切碎的字符串流。
    这个类充当一个"收集器"，负责把同一个 Tool 的参数碎片拼接成完整的 JSON。
    """

    name: str = 'unknown_tool'  # 工具名，可能在后续增量中才出现。
    arguments_parts: list[str] = field(default_factory=list)  # 参数 JSON 分片列表。

    def merge_delta(self, *, tool_name: str | None, arguments_delta: str) -> None:
        """合并一次 tool_call 增量。"""
        """接收到 SSE 事件流中的增量数据时，更新当前收集器的状态。"""
        if self.name == 'unknown_tool' and tool_name:
            self.name = tool_name
        if arguments_delta:
            self.arguments_parts.append(arguments_delta)

    def build_arguments(self) -> JSONDict:
        """把参数分片拼接并解析为 dict。"""
        """流结束后，将收集到的字符串碎片合并，并解析为最终的字典。"""
        arguments_text = ''.join(self.arguments_parts).strip()
        if not arguments_text:
            return {}
        return OpenAIClient._parse_tool_arguments(arguments_text)


# ---------------------------------------------------------------------------
# 客户端实现
# ---------------------------------------------------------------------------


class OpenAIClient:
    """最小可运行的 OpenAI-compatible 客户端。"""

    model_config: ModelConfig  # 客户端固定模型配置。

    def __init__(self, model_config: ModelConfig) -> None:
        self.model_config = model_config

    def complete(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: OutputSchemaConfig | None = None,
    ) -> OneTurnResponse:
        """执行一次非流式模型调用并返回标准化结果。
            messages: 模型对话消息列表，每条消息是一个 dict，至少包含 'role' 和 'content' 字段。
            tools: 可选的工具定义列表，每个工具是一个 dict，至少包含 'name' 和 'description' 字段。
            output_schema: 可选的输出格式定义，指定模型响应应该符合的 JSON Schema。
        """
        payload = self._build_payload(
            messages=messages,
            tools=tools,
            stream=False,
            output_schema=output_schema,
        )
        response_payload = self._request_json(payload)
        return self._parse_one_turn_response(response_payload)

    def stream(
        self,
        messages: list[JSONDict],  # 消息列表，包含对话历史
        tools: list[JSONDict] | None = None,  # 可选的工具列表，用于扩展模型功能
        *,
        output_schema: OutputSchemaConfig | None = None,  # 输出模式配置，可选
    ) -> Iterator[StreamEvent]:
        """执行一次流式模型调用并持续输出标准化事件。
            流式调用会返回一系列事件，每个事件表示模型生成的部分内容、工具调用增量或使用情况。
        """
        payload = self._build_payload(
            messages=messages,
            tools=tools,
            stream=True,
            output_schema=output_schema,
        )
        req = self._build_request(payload)

        try:
            with request.urlopen(req, timeout=self.model_config.timeout_seconds) as response:
                # 先发起一个固定起始事件，调用方更容易写统一状态机。
                yield StreamEvent(type='message_start')
                for event_payload in self._iter_sse_payloads(response):
                    yield from self._parse_stream_payload(event_payload)
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            self._raise_request_error(
                exc,
                base_url=self.model_config.base_url,
                timeout_seconds=self.model_config.timeout_seconds,
            )

    def complete_stream(
        self,
        messages: list[JSONDict],
        tools: list[JSONDict] | None = None,
        *,
        output_schema: OutputSchemaConfig | None = None,
    ) -> OneTurnResponse:
        """把流式事件聚合为最终 OneTurnResponse。"""
        content_parts: list[str] = []
        finish_reason: str | None = None
        usage = TokenUsage()

        # 使用有序记录保持 tool_calls 的输出顺序稳定。
        tool_order: list[str] = []
        tool_state: dict[str, _ToolCallBuildState] = {}
        index_to_call_id: dict[int, str] = {}

        stream_events = self.stream(
            messages=messages,
            tools=tools,
            output_schema=output_schema,
        )
        for event in stream_events:
            if event.type == 'content_delta' and event.delta:
                content_parts.append(event.delta)
                continue

            if event.type == 'tool_call_delta':
                index = event.tool_call_index if event.tool_call_index is not None else 0
                call_id = event.tool_call_id
                if call_id is None:
                    # 有些后端在后续增量里只返回 index，不再重复返回 id。
                    call_id = index_to_call_id.get(index, f'call_{index}')
                else:
                    index_to_call_id[index] = call_id

                slot = tool_state.get(call_id)
                if slot is None:
                    slot = _ToolCallBuildState()
                    tool_state[call_id] = slot
                    tool_order.append(call_id)

                slot.merge_delta(
                    tool_name=event.tool_name,
                    arguments_delta=event.arguments_delta,
                )
                continue

            if event.type == 'message_stop':
                finish_reason = event.finish_reason
                continue

            if event.type == 'usage':
                usage = event.usage

        tool_calls: list[ToolCall] = []
        for call_id in tool_order:
            slot = tool_state[call_id]
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=slot.name,
                    arguments=slot.build_arguments(),
                )
            )

        return OneTurnResponse(
            content=''.join(content_parts),
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _build_payload(
        self,
        *,
        messages: list[JSONDict],
        tools: list[JSONDict] | None,
        stream: bool,
        output_schema: OutputSchemaConfig | None,
    ) -> JSONDict:
        """构造发送给 /chat/completions 的请求体。"""
        payload: JSONDict = {
            'model': self.model_config.model,
            'messages': [dict(item) for item in messages if isinstance(item, dict)], # 只保留 dict，避免脏输入污染请求体。
            'temperature': self.model_config.temperature,
        }

        if tools:
            payload['tools'] = [dict(item) for item in tools if isinstance(item, dict)]
            payload['tool_choice'] = 'auto'

        if stream:
            payload['stream'] = True
            payload['stream_options'] = {'include_usage': True} # 让后端在结束事件里带回 usage，便于最终统计。

        # 指定输出格式，告诉大模型后端我们希望它按照某个 JSON Schema 来组织响应内容，这样可以提升结构化输出的质量和解析的鲁棒性。
        response_format = self._build_response_format(output_schema)
        if response_format is not None:
            payload['response_format'] = response_format
        return payload

    @staticmethod
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

    def _request_json(self, payload: JSONDict) -> JSONDict:
        """发送请求并返回 JSON 对象。"""
        req = self._build_request(payload)

        try:
            with request.urlopen(req, timeout=self.model_config.timeout_seconds) as response:
                body = response.read()
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            self._raise_request_error(
                exc,
                base_url=self.model_config.base_url,
                timeout_seconds=self.model_config.timeout_seconds,
            )

        try:
            decoded_body = json.loads(body.decode('utf-8')) # Deserialize the response body from JSON, ensuring it's a dict.
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError(
                'Model backend returned invalid JSON'
            ) from exc

        if not isinstance(decoded_body, dict):
            raise OpenAIResponseError(
                'Model backend returned malformed JSON payload'
            )
        return decoded_body

    def _build_request(self, payload: JSONDict) -> request.Request:
        """构造标准的 HTTP 请求对象，包含 URL、方法 POST、必要的头部和 JSON 编码的请求体。"""
        return request.Request(
            _join_url(self.model_config.base_url, '/chat/completions'),
            data=json.dumps(payload).encode('utf-8'), # Serialize the payload to JSON and encode it as bytes.
            headers={
                'Authorization': f'Bearer {self.model_config.api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )

    @classmethod
    def _raise_request_error(
        cls,
        exc: error.HTTPError | error.URLError | TimeoutError,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        """把底层网络异常统一映射到客户端异常族。"""
        if isinstance(exc, error.HTTPError):
            detail = cls._http_error_detail(exc)
            raise OpenAIResponseError(
                f'HTTP {exc.code} from model backend: {detail}',
                status_code=exc.code,
                detail=detail,
            ) from exc

        if isinstance(exc, error.URLError):
            if isinstance(exc.reason, TimeoutError):
                raise OpenAITimeoutError(
                    f'Model request timed out after {timeout_seconds} seconds'
                ) from exc
            raise OpenAIConnectionError(
                f'Unable to reach model backend at {base_url}: {exc.reason}'
            ) from exc

        raise OpenAITimeoutError(
            f'Model request timed out after {timeout_seconds} seconds'
        ) from exc

    @staticmethod
    def _http_error_detail(exc: error.HTTPError) -> str:
        """尽量读取 HTTP 错误体，便于定位后端问题。"""
        try:
            detail = exc.read().decode('utf-8', errors='replace')
        except Exception:
            detail = ''
        return detail or exc.reason or exc.msg or 'unknown error'

    def _parse_one_turn_response(self, payload: JSONDict) -> OneTurnResponse:
        """把后端响应解析为 OneTurnResponse。"""
        first_choice, message = self._extract_choice_and_message(payload)
        return OneTurnResponse(
            content=self._normalize_content(message.get('content')),
            tool_calls=tuple(self._parse_tool_calls_from_message(message)),
            finish_reason=self._normalize_finish_reason(first_choice.get('finish_reason')),
            usage=self._parse_usage(payload.get('usage')),
        )

    @staticmethod
    def _extract_choice_and_message(payload: JSONDict) -> tuple[JSONDict, JSONDict]:
        """提取第一条 choice 及 message，并做结构校验。"""
        choices = payload.get('choices')
        if not isinstance(choices, list) or not choices:
            raise OpenAIResponseError('Model backend returned no choices')

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise OpenAIResponseError('Model backend returned malformed choice data')

        message = first_choice.get('message')
        if not isinstance(message, dict):
            raise OpenAIResponseError('Model backend returned no assistant message')

        return first_choice, message

    @staticmethod
    def _normalize_content(content: Any) -> str:
        """把各种格式的 content 统一转换为字符串。"""
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
                    if isinstance(item.get('text'), str):
                        parts.append(item['text'])
                        continue
                    parts.append(json.dumps(item, ensure_ascii=True))
                    continue
                parts.append(str(item))
            return ''.join(parts)
        return str(content)

    def _parse_tool_calls_from_message(self, message: JSONDict) -> list[ToolCall]:
        """从 message 中解析工具调用，兼容新旧字段。"""
        raw_tool_calls = message.get('tool_calls')
        if isinstance(raw_tool_calls, list):
            tool_calls: list[ToolCall] = []
            for index, raw_call in enumerate(raw_tool_calls):
                if not isinstance(raw_call, dict):
                    raise OpenAIResponseError('Malformed tool call payload')
                tool_calls.append(self._parse_single_tool_call(raw_call, index))
            return tool_calls

        function_call = message.get('function_call')
        if isinstance(function_call, dict):
            return [self._parse_legacy_function_call(function_call)]

        return []

    def _parse_single_tool_call(self, raw_call: JSONDict, index: int) -> ToolCall:
        """解析单个新格式 tool_call。"""
        function_block = raw_call.get('function')
        if not isinstance(function_block, dict):
            raise OpenAIResponseError('Malformed tool call function payload')

        name = function_block.get('name')
        if not isinstance(name, str) or not name:
            raise OpenAIResponseError('Tool call missing function name')

        call_id = raw_call.get('id')
        if not isinstance(call_id, str) or not call_id:
            call_id = f'call_{index}'

        arguments = self._parse_tool_arguments(function_block.get('arguments'))
        return ToolCall(id=call_id, name=name, arguments=arguments)

    @staticmethod
    def _parse_tool_arguments(raw_arguments: Any) -> JSONDict:
        """解析工具调用的参数，把工具 arguments 解析为 dict。"""
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
                raise OpenAIResponseError(
                    f'Invalid tool arguments payload: {raw_arguments!r}'
                ) from exc
            if not isinstance(parsed, dict):
                raise OpenAIResponseError('Tool arguments must decode to JSON object')
            return parsed
        raise OpenAIResponseError(
            f'Unsupported tool arguments payload type: {type(raw_arguments).__name__}'
        )

    def _parse_legacy_function_call(self, function_call: JSONDict) -> ToolCall:
        """解析旧格式 function_call。"""
        name = function_call.get('name')
        if not isinstance(name, str) or not name:
            raise OpenAIResponseError('Function call missing name')
        arguments = self._parse_tool_arguments(function_call.get('arguments'))
        return ToolCall(id='call_0', name=name, arguments=arguments)

    @staticmethod
    def _normalize_finish_reason(value: Any) -> str | None:
        """把 finish_reason 统一成 str 或 None。"""
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _parse_usage(payload: Any) -> TokenUsage:
        """兼容多种字段命名并转换为 TokenUsage。"""
        if not isinstance(payload, dict):
            return TokenUsage()

        normalized: JSONDict = dict(payload)
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

        completion_details = payload.get('completion_tokens_details')
        if (
            'reasoning_tokens' not in normalized
            and isinstance(completion_details, dict)
            and completion_details.get('reasoning_tokens') is not None
        ):
            normalized['reasoning_tokens'] = completion_details.get('reasoning_tokens')

        return TokenUsage.from_dict(normalized)

    def _iter_sse_payloads(self, response: Any) -> Iterator[JSONDict]:
        """从 SSE 响应中按事件读取 JSON payload。"""
        buffer: list[str] = []
        while True:
            line = response.readline()
            if not line:
                break

            text = line.decode('utf-8', errors='replace') if isinstance(line, bytes) else str(line)
            stripped = text.strip()

            if not stripped:
                if not buffer:
                    continue
                payload = self._decode_sse_payload(buffer)
                buffer.clear()
                if payload is not None:
                    yield payload
                continue

            if stripped.startswith('data:'):
                buffer.append(stripped[5:].strip())

        if buffer:
            payload = self._decode_sse_payload(buffer)
            if payload is not None:
                yield payload

    def _decode_sse_payload(self, lines: list[str]) -> JSONDict | None:
        """解析单个 SSE 事件块。"""
        joined = '\n'.join(lines).strip()
        if not joined or joined == '[DONE]':
            return None

        try:
            decoded = json.loads(joined)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError(
                f'Model backend returned invalid stream JSON chunk: {joined!r}'
            ) from exc

        if not isinstance(decoded, dict):
            raise OpenAIResponseError('Model backend returned malformed stream payload')
        return decoded

    def _parse_stream_payload(self, payload: JSONDict) -> Iterator[StreamEvent]:
        """把单个流式 payload 转换为 StreamEvent。"""
        usage = self._parse_usage(payload.get('usage'))
        if self._has_usage_value(usage):
            yield StreamEvent(
                type='usage',
                usage=usage,
                raw_event=dict(payload),
            )

        choices = payload.get('choices')
        if choices is None:
            return
        if not isinstance(choices, list):
            raise OpenAIResponseError('Model backend returned malformed stream choices')

        for choice in choices:
            if not isinstance(choice, dict):
                raise OpenAIResponseError('Model backend returned malformed stream choice')

            delta = choice.get('delta')
            if delta is None:
                delta = {}
            if not isinstance(delta, dict):
                raise OpenAIResponseError('Model backend returned malformed stream delta')

            if 'content' in delta:
                content_delta = self._normalize_content(delta.get('content'))
                if content_delta:
                    yield StreamEvent(
                        type='content_delta',
                        delta=content_delta,
                        raw_event=dict(choice),
                    )

            raw_tool_calls = delta.get('tool_calls')
            if raw_tool_calls is not None:
                if not isinstance(raw_tool_calls, list):
                    raise OpenAIResponseError('Model backend returned malformed stream tool_calls')
                for raw_tool_call in raw_tool_calls:
                    yield self._parse_stream_tool_call_delta(raw_tool_call)

            finish_reason = choice.get('finish_reason')
            if finish_reason is not None:
                yield StreamEvent(
                    type='message_stop',
                    finish_reason=self._normalize_finish_reason(finish_reason),
                    raw_event=dict(choice),
                )

    @staticmethod
    def _has_usage_value(usage: TokenUsage) -> bool:
        """判断 usage 是否包含有效统计值。"""
        return any(
            (
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_creation_input_tokens,
                usage.cache_read_input_tokens,
                usage.reasoning_tokens,
            )
        )

    def _parse_stream_tool_call_delta(self, raw_tool_call: Any) -> StreamEvent:
        """把 tool_call 增量片段标准化为 StreamEvent。"""
        if not isinstance(raw_tool_call, dict):
            raise OpenAIResponseError('Model backend returned malformed stream tool_call item')

        function_block = raw_tool_call.get('function')
        if function_block is None:
            function_block = {}
        if not isinstance(function_block, dict):
            raise OpenAIResponseError('Model backend returned malformed stream tool_call function')

        tool_call_index = raw_tool_call.get('index')
        if not isinstance(tool_call_index, int):
            tool_call_index = 0

        tool_call_id = raw_tool_call.get('id')
        if not isinstance(tool_call_id, str) or not tool_call_id:
            tool_call_id = None

        tool_name = function_block.get('name')
        if not isinstance(tool_name, str) or not tool_name:
            tool_name = None

        arguments_delta = function_block.get('arguments')
        if not isinstance(arguments_delta, str):
            arguments_delta = ''

        return StreamEvent(
            type='tool_call_delta',
            tool_call_index=tool_call_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_delta=arguments_delta,
            raw_event=dict(raw_tool_call),
        )