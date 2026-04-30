"""client 模块内部 OpenAI 兼容实现（单文件精简版）。

按约定将请求构造、HTTP 传输、响应解析与客户端编排收拢到同一文件，
避免跨多文件跳转带来的阅读负担。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterator
from urllib import error, request

from core_contracts.client_contracts import ClientExecutionError, ClientRequest
from core_contracts.messaging import OneTurnResponse, StreamEvent, ToolCall
from core_contracts.model import ModelConfig, StructuredOutputSpec
from core_contracts.primitives import JSONDict, TokenUsage


class EndpointResolver:
	"""根据模型配置解析后端接口地址。"""

	def __init__(self, model_config: ModelConfig) -> None:
		self._model_config = model_config

	def chat_completions_url(self) -> str:
		return f"{self._model_config.base_url.rstrip('/')}/chat/completions"


class HttpRequestFactory:
	"""负责构造 urllib Request 对象。"""

	def __init__(self, endpoint_resolver: EndpointResolver, model_config: ModelConfig) -> None:
		self._endpoint_resolver = endpoint_resolver
		self._model_config = model_config

	def build(self, payload: JSONDict) -> request.Request:
		return request.Request(
			self._endpoint_resolver.chat_completions_url(),
			data=json.dumps(payload).encode('utf-8'),
			headers={
				'Authorization': f'Bearer {self._model_config.api_key}',
				'Content-Type': 'application/json',
			},
			method='POST',
		)


class HttpTransport:
	"""负责执行 HTTP 调用并返回 JSON 对象。"""

	def __init__(
		self,
		request_factory: HttpRequestFactory,
		model_config: ModelConfig,
		urlopen: Callable[..., Any] | None = None,
	) -> None:
		self._request_factory = request_factory
		self._model_config = model_config
		self._urlopen = urlopen

	def post_json(self, payload: JSONDict) -> JSONDict:
		req = self._request_factory.build(payload)
		opener = self._urlopen or request.urlopen
		try:
			with opener(req, timeout=self._model_config.timeout_seconds) as response:
				body = response.read()
		except (error.HTTPError, error.URLError, TimeoutError) as exc:
			raise ClientExecutionError(f'HTTP request failed: {exc}') from exc
		return self._load_json_object(body)

	def open_event_stream(self, payload: JSONDict) -> Any:
		req = self._request_factory.build(payload)
		opener = self._urlopen or request.urlopen
		try:
			return opener(req, timeout=self._model_config.timeout_seconds)
		except (error.HTTPError, error.URLError, TimeoutError) as exc:
			raise ClientExecutionError(f'HTTP stream request failed: {exc}') from exc

	@staticmethod
	def _load_json_object(body: bytes) -> JSONDict:
		try:
			decoded = json.loads(body.decode('utf-8'))
		except json.JSONDecodeError as exc:
			raise ClientExecutionError('Model backend returned invalid JSON') from exc
		if not isinstance(decoded, dict):
			raise ClientExecutionError('Model backend returned malformed JSON payload')
		return decoded


class PayloadBuilder:
	"""把稳定契约 DTO 转换为 OpenAI 兼容请求体。"""

	def __init__(self, model_config: ModelConfig) -> None:
		self._model_config = model_config

	def build(self, client_request: ClientRequest, *, stream: bool) -> JSONDict:
		payload: JSONDict = {
			'model': self._model_config.model,
			'messages': [dict(item) for item in client_request.messages],
			'temperature': self._model_config.temperature,
		}
		if client_request.tools:
			payload['tools'] = [dict(item) for item in client_request.tools]
			payload['tool_choice'] = 'auto'
		if stream:
			payload['stream'] = True
			payload['stream_options'] = {'include_usage': True}
		response_format = self._build_response_format(client_request.output_schema)
		if response_format is not None:
			payload['response_format'] = response_format
		return payload

	@staticmethod
	def _build_response_format(output_schema: StructuredOutputSpec | None) -> JSONDict | None:
		if output_schema is None:
			return None
		if output_schema.get('type') == 'json_schema':
			return {'type': 'json_schema', 'json_schema': dict(output_schema.get('json_schema', {}))}
		return {'type': 'json_object'}


class CompletionParser:
	"""负责把完整响应载荷解析为 OneTurnResponse。"""

	def parse(self, payload: JSONDict) -> OneTurnResponse:
		choices = payload.get('choices')
		if not isinstance(choices, list) or not choices:
			raise ClientExecutionError('Model backend returned no choices')
		first_choice = choices[0]
		if not isinstance(first_choice, dict):
			raise ClientExecutionError('Model backend returned malformed choice data')
		message = first_choice.get('message')
		if not isinstance(message, dict):
			raise ClientExecutionError('Model backend returned no assistant message')
		content = message.get('content')
		if content is None:
			content_text = ''
		elif isinstance(content, str):
			content_text = content
		else:
			content_text = str(content)
		return OneTurnResponse(
			content=content_text,
			tool_calls=tuple(self._parse_tool_calls(message.get('tool_calls'))),
			finish_reason=str(first_choice.get('finish_reason') or 'stop'),
			usage=self.parse_usage(payload.get('usage')),
		)

	def parse_usage(self, usage_payload: object) -> TokenUsage:
		if not isinstance(usage_payload, dict):
			return TokenUsage()
		return TokenUsage(
			input_tokens=self._safe_int(usage_payload.get('prompt_tokens')),
			output_tokens=self._safe_int(usage_payload.get('completion_tokens')),
			total_tokens=self._safe_int(usage_payload.get('total_tokens')),
		)

	@staticmethod
	def _safe_int(value: object) -> int:
		return int(value) if isinstance(value, (int, float)) else 0

	def _parse_tool_calls(self, payload: object) -> list[ToolCall]:
		if not isinstance(payload, list):
			return []
		tool_calls: list[ToolCall] = []
		for item in payload:
			if not isinstance(item, dict):
				continue
			function = item.get('function')
			if not isinstance(function, dict):
				continue
			name = function.get('name')
			if not isinstance(name, str) or not name.strip():
				continue
			arguments = self._parse_arguments(function.get('arguments', '{}'))
			tool_calls.append(ToolCall(id=str(item.get('id') or ''), name=name, arguments=arguments))
		return tool_calls

	def _parse_arguments(self, raw_arguments: object) -> JSONDict:
		if isinstance(raw_arguments, dict):
			return dict(raw_arguments)
		if isinstance(raw_arguments, str):
			if not raw_arguments.strip():
				return {}
			try:
				decoded = json.loads(raw_arguments)
			except json.JSONDecodeError as exc:
				raise ClientExecutionError(f'Invalid tool arguments payload: {raw_arguments!r}') from exc
			if isinstance(decoded, dict):
				return decoded
			raise ClientExecutionError('Tool arguments must decode to JSON object')
		raise ClientExecutionError('Tool arguments payload has invalid type')


class SSEReader:
	"""负责把 SSE 字节流解码为 JSON 载荷。"""

	def iter_payloads(self, response: Any) -> Iterator[JSONDict]:
		for raw_line in response:
			if isinstance(raw_line, bytes):
				line = raw_line.decode('utf-8', errors='replace').strip()
			else:
				line = str(raw_line).strip()
			if not line or not line.startswith('data:'):
				continue
			data = line[5:].strip()
			if data == '[DONE]':
				break
			try:
				decoded = json.loads(data)
			except json.JSONDecodeError as exc:
				raise ClientExecutionError(f'Invalid stream JSON payload: {data!r}') from exc
			if isinstance(decoded, dict):
				yield decoded


@dataclass
class _ToolCallPartial:
	id: str = ''
	name: str = ''
	arguments_text: str = ''


class StreamEventParser:
	"""负责把流式 JSON 块解析为统一 StreamEvent。"""

	def __init__(self, completion_parser: CompletionParser) -> None:
		self._completion_parser = completion_parser

	def parse_payload(self, payload: JSONDict) -> Iterator[StreamEvent]:
		choices = payload.get('choices')
		if not isinstance(choices, list):
			return
		for choice in choices:
			if not isinstance(choice, dict):
				continue
			delta = choice.get('delta')
			if isinstance(delta, dict):
				text = delta.get('content')
				if isinstance(text, str) and text:
					yield StreamEvent(type='content_delta', delta=text)
				tool_calls = delta.get('tool_calls')
				if isinstance(tool_calls, list):
					for item in tool_calls:
						if not isinstance(item, dict):
							continue
						index = int(item.get('index') or 0)
						fn = item.get('function')
						if not isinstance(fn, dict):
							continue
						name = fn.get('name') if isinstance(fn.get('name'), str) else None
						args = fn.get('arguments') if isinstance(fn.get('arguments'), str) else ''
						yield StreamEvent(type='tool_call_delta', tool_call_index=index, tool_name=name, tool_arguments_delta=args)
			finish_reason = choice.get('finish_reason')
			if isinstance(finish_reason, str) and finish_reason:
				yield StreamEvent(type='message_stop', finish_reason=finish_reason)
		usage = payload.get('usage')
		if isinstance(usage, dict):
			yield StreamEvent(type='usage', usage=self._completion_parser.parse_usage(usage))


class StreamResultAggregator:
	"""负责把流式事件聚合为最终 OneTurnResponse。"""

	def aggregate(self, events: Iterator[StreamEvent]) -> OneTurnResponse:
		content_parts: list[str] = []
		finish_reason = 'stop'
		usage = TokenUsage()
		tool_state: dict[int, _ToolCallPartial] = {}
		for event in events:
			if event.type == 'content_delta' and isinstance(event.delta, str):
				content_parts.append(event.delta)
			elif event.type == 'message_stop' and isinstance(event.finish_reason, str):
				finish_reason = event.finish_reason
			elif event.type == 'usage' and event.usage is not None:
				usage = event.usage
			elif event.type == 'tool_call_delta' and event.tool_call_index is not None:
				index = int(event.tool_call_index)
				state = tool_state.setdefault(index, _ToolCallPartial())
				if isinstance(event.tool_name, str) and event.tool_name:
					state.name = event.tool_name
				if isinstance(event.tool_call_id, str) and event.tool_call_id:
					state.id = event.tool_call_id
				if isinstance(event.tool_arguments_delta, str):
					state.arguments_text += event.tool_arguments_delta
		tool_calls: list[ToolCall] = []
		for index in sorted(tool_state.keys()):
			state = tool_state[index]
			if not state.name:
				continue
			tool_calls.append(
				ToolCall(
					id=state.id,
					name=state.name,
					arguments=self._parse_tool_arguments(state.arguments_text),
				)
			)
		return OneTurnResponse(
			content=''.join(content_parts),
			tool_calls=tuple(tool_calls),
			finish_reason=finish_reason,
			usage=usage,
		)

	def _parse_tool_arguments(self, raw_arguments: str) -> JSONDict:
		if not raw_arguments.strip():
			return {}
		try:
			decoded = json.loads(raw_arguments)
		except json.JSONDecodeError as exc:
			raise ClientExecutionError(f'Invalid streamed tool arguments payload: {raw_arguments!r}') from exc
		if not isinstance(decoded, dict):
			raise ClientExecutionError('Streamed tool arguments must decode to JSON object')
		return decoded


class OpenAIClient:
	"""OpenAI 兼容客户端。对外统一使用 complete/stream/complete_stream。"""

	def __init__(
		self,
		*,
		payload_builder: PayloadBuilder,
		transport: HttpTransport,
		completion_parser: CompletionParser,
		sse_reader: SSEReader,
		stream_parser: StreamEventParser,
		stream_aggregator: StreamResultAggregator,
	) -> None:
		self._payload_builder = payload_builder
		self._completion_parser = completion_parser
		self._sse_reader = sse_reader
		self._stream_parser = stream_parser
		self._stream_aggregator = stream_aggregator
		self._transport = transport

	def complete(
		self,
		messages: list[JSONDict],
		tools: list[JSONDict] | None = None,
		*,
		output_schema: StructuredOutputSpec | None = None,
	) -> OneTurnResponse:
		client_request = ClientRequest.from_legacy(messages, tools, output_schema)
		payload = self._payload_builder.build(client_request, stream=False)
		response_payload = self._transport.post_json(payload)
		return self._completion_parser.parse(response_payload)

	def stream(
		self,
		messages: list[JSONDict],
		tools: list[JSONDict] | None = None,
		*,
		output_schema: StructuredOutputSpec | None = None,
	) -> Iterator[StreamEvent]:
		client_request = ClientRequest.from_legacy(messages, tools, output_schema)
		payload = self._payload_builder.build(client_request, stream=True)
		with self._transport.open_event_stream(payload) as response:
			yield StreamEvent(type='message_start')
			for event_payload in self._sse_reader.iter_payloads(response):
				for event in self._stream_parser.parse_payload(event_payload):
					yield event

	def complete_stream(
		self,
		messages: list[JSONDict],
		tools: list[JSONDict] | None = None,
		*,
		output_schema: StructuredOutputSpec | None = None,
	) -> OneTurnResponse:
		return self._stream_aggregator.aggregate(self.stream(messages, tools, output_schema=output_schema))


__all__ = [
	'EndpointResolver',
	'HttpRequestFactory',
	'HttpTransport',
	'PayloadBuilder',
	'CompletionParser',
	'SSEReader',
	'StreamEventParser',
	'StreamResultAggregator',
	'OpenAIClient',
]

