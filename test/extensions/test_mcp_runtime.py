"""ISSUE-021 MCP Runtime 单元测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.mcp_models import MCPServerProfile, MCPTool, MCPTransportError
from tools.mcp_runtime import MCPRuntime


class MCPRuntimeTests(unittest.TestCase):
    def _write_manifest(self, workspace: Path, filename: str, payload: dict[str, object]) -> None:
        manifest_dir = workspace / '.claw' / 'mcp'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _write_fake_stdio_server(self, workspace: Path) -> Path:
        server_path = workspace / 'fake_mcp_server.py'
        server_path.write_text(
            (
                'import json, sys\n'
                'RESOURCES = [{"uri": "mcp://remote/notes", "name": "Remote Notes", "mimeType": "text/plain"}]\n'
                'TOOLS = [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]\n'
                'def read_message():\n'
                '    header = b""\n'
                '    while b"\\r\\n\\r\\n" not in header:\n'
                '        chunk = sys.stdin.buffer.read(1)\n'
                '        if not chunk:\n'
                '            return None\n'
                '        header += chunk\n'
                '    header_blob, _, remainder = header.partition(b"\\r\\n\\r\\n")\n'
                '    content_length = 0\n'
                '    for raw_line in header_blob.decode("ascii").split("\\r\\n"):\n'
                '        name, _, value = raw_line.partition(":")\n'
                '        if name.lower() == "content-length":\n'
                '            content_length = int(value.strip())\n'
                '            break\n'
                '    body = remainder\n'
                '    while len(body) < content_length:\n'
                '        chunk = sys.stdin.buffer.read(content_length - len(body))\n'
                '        if not chunk:\n'
                '            break\n'
                '        body += chunk\n'
                '    if len(body) < content_length:\n'
                '        return None\n'
                '    return json.loads(body[:content_length].decode("utf-8"))\n'
                'def write_message(payload):\n'
                '    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")\n'
                '    sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)\n'
                '    sys.stdout.buffer.flush()\n'
                'while True:\n'
                '    message = read_message()\n'
                '    if message is None:\n'
                '        break\n'
                '    method = message.get("method")\n'
                '    if method == "initialize":\n'
                '        write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"protocolVersion": "2025-11-25", "capabilities": {"resources": {}, "tools": {}}, "serverInfo": {"name": "fake-remote", "version": "1.0.0"}}})\n'
                '        continue\n'
                '    if method == "notifications/initialized":\n'
                '        continue\n'
                '    if method == "resources/list":\n'
                '        write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"resources": RESOURCES}})\n'
                '        continue\n'
                '    if method == "resources/read":\n'
                '        uri = message.get("params", {}).get("uri")\n'
                '        text = "remote notes via framed stdio" if uri == "mcp://remote/notes" else "unknown resource"\n'
                '        write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}})\n'
                '        continue\n'
                '    if method == "tools/list":\n'
                '        write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": TOOLS}})\n'
                '        continue\n'
                '    if method == "tools/call":\n'
                '        params = message.get("params", {})\n'
                '        text = params.get("arguments", {}).get("text", "")\n'
                '        write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": {"content": [{"type": "text", "text": "echo:" + text}], "isError": False}})\n'
                '        continue\n'
                '    write_message({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "Method not found"}})\n'
            ),
            encoding='utf-8',
        )
        return server_path

    def test_runtime_discovers_and_reads_local_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'notes.txt').write_text('mcp notes\n', encoding='utf-8')
            self._write_manifest(
                workspace,
                'workspace.json',
                {
                    'servers': [
                        {
                            'name': 'workspace',
                            'resources': [
                                {'uri': 'mcp://workspace/notes', 'name': 'Notes', 'path': 'notes.txt'},
                                {'uri': 'mcp://workspace/inline', 'name': 'Inline', 'text': 'inline body'},
                            ],
                        }
                    ]
                },
            )

            runtime = MCPRuntime.from_workspace(workspace)

            resources = runtime.list_resources()
            inline_body = runtime.read_resource('mcp://workspace/inline')
            file_body = runtime.read_resource('mcp://workspace/notes')

        self.assertEqual([item.uri for item in resources], ['mcp://workspace/notes', 'mcp://workspace/inline'])
        self.assertEqual(inline_body, 'inline body')
        self.assertIn('mcp notes', file_body)

    def test_runtime_lists_remote_resources_and_calls_transport_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            server_path = self._write_fake_stdio_server(workspace)
            self._write_manifest(
                workspace,
                'remote.json',
                {
                    'mcpServers': {
                        'remote': {
                            'command': sys.executable,
                            'args': ['-u', str(server_path)],
                        }
                    }
                },
            )

            runtime = MCPRuntime.from_workspace(workspace)
            resources = runtime.list_resources()
            resource_text = runtime.read_resource('mcp://remote/notes')
            tools = runtime.list_tools()
            tool_result = runtime.call_tool('echo', arguments={'text': 'hello'})

        self.assertEqual(len(runtime.servers), 1)
        self.assertEqual([item.uri for item in resources], ['mcp://remote/notes'])
        self.assertIn('remote notes via framed stdio', resource_text)
        self.assertEqual([item.name for item in tools], ['echo'])
        self.assertEqual(tool_result.server_name, 'remote')
        self.assertEqual(tool_result.tool_name, 'echo')
        self.assertFalse(tool_result.is_error)
        self.assertIn('echo:hello', tool_result.content)

    def test_invalid_server_failure_is_trackable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'broken.json',
                {
                    'mcpServers': {
                        'broken': {
                            'command': str(workspace / 'missing-mcp-server.exe'),
                        }
                    }
                },
            )

            runtime = MCPRuntime.from_workspace(workspace)

            with self.assertRaises(MCPTransportError) as raised:
                runtime.list_tools(server_name='broken')

        self.assertEqual(raised.exception.server_name, 'broken')
        self.assertEqual(raised.exception.method, 'tools/list')
        self.assertIn('broken', str(raised.exception))

    def test_runtime_caches_remote_tool_list_per_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = MCPRuntime(
                workspace=workspace,
                servers=(MCPServerProfile(name='remote', transport='stdio', command='fake-mcp-server'),),
            )
            runtime._transport_client = mock.Mock()
            runtime._transport_client.request = mock.Mock(return_value={
                'tools': [
                    {
                        'name': 'echo',
                        'description': 'Echo text',
                        'inputSchema': {
                            'type': 'object',
                            'properties': {'text': {'type': 'string'}},
                            'required': ['text'],
                        },
                    }
                ]
            })

            first = runtime.list_tools()
            second = runtime.list_tools()

        self.assertEqual([tool.name for tool in first], ['echo'])
        self.assertEqual([tool.name for tool in second], ['echo'])
        runtime._transport_client.request.assert_called_once_with(runtime.servers[0], 'tools/list', {})

    def test_call_tool_discovers_remote_tool_on_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = MCPRuntime(
                workspace=workspace,
                servers=(MCPServerProfile(name='remote', transport='stdio', command='fake-mcp-server'),),
            )

            def _request(server, method, params):
                if method == 'tools/list':
                    return {
                        'tools': [
                            {
                                'name': 'echo',
                                'description': 'Echo text',
                                'inputSchema': {
                                    'type': 'object',
                                    'properties': {'text': {'type': 'string'}},
                                    'required': ['text'],
                                },
                            }
                        ]
                    }
                if method == 'tools/call':
                    return {
                        'content': [{'type': 'text', 'text': 'echo:' + params.get('arguments', {}).get('text', '')}],
                        'isError': False,
                    }
                raise AssertionError(f'unexpected MCP method: {method}')

            runtime._transport_client = mock.Mock()
            runtime._transport_client.request = mock.Mock(side_effect=_request)

            result = runtime.call_tool('echo', arguments={'text': 'hello'})

        methods = [call.args[1] for call in runtime._transport_client.request.call_args_list]
        self.assertEqual(methods, ['tools/list', 'tools/call'])
        self.assertEqual(result.tool_name, 'echo')
        self.assertEqual(result.server_name, 'remote')
        self.assertIn('echo:hello', result.content)

    def test_render_tool_index_includes_schema_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = MCPRuntime(
                workspace=workspace,
                servers=(MCPServerProfile(name='remote', transport='stdio', command='fake-mcp-server'),),
            )
            runtime._tool_cache_by_server['remote'] = (
                MCPTool(
                    name='echo',
                    server_name='remote',
                    description='Echo text',
                    input_schema={
                        'type': 'object',
                        'properties': {'text': {'type': 'string'}},
                        'required': ['text'],
                    },
                ),
            )

            rendered = runtime.render_tool_index(server_name='remote')

        self.assertIn('- echo; server=remote', rendered)
        self.assertIn('description: Echo text', rendered)
        self.assertIn('type: object', rendered)
        self.assertIn('required: text', rendered)
        self.assertIn('properties: text:string', rendered)

    def test_search_capabilities_returns_ranked_handles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = MCPRuntime(
                workspace=workspace,
                servers=(MCPServerProfile(name='remote', transport='stdio', command='fake-mcp-server'),),
            )
            runtime._tool_cache_by_server['remote'] = (
                MCPTool(
                    name='echo',
                    server_name='remote',
                    description='Echo text back to the user.',
                    input_schema={'type': 'object', 'properties': {'text': {'type': 'string'}}},
                ),
                MCPTool(
                    name='tavily_search',
                    server_name='remote',
                    description='Search the web for current information and news.',
                    input_schema={
                        'type': 'object',
                        'properties': {'query': {'type': 'string'}},
                        'required': ['query'],
                    },
                ),
            )

            capabilities = runtime.search_capabilities(query='current news', server_name='remote')

        self.assertEqual(len(capabilities), 1)
        self.assertEqual(capabilities[0].handle, 'mcp:remote:tavily_search')
        self.assertEqual(capabilities[0].required_parameters, ('query',))
        self.assertEqual(capabilities[0].risk_level, 'read')

    def test_render_capability_index_includes_handle_and_parameter_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = MCPRuntime(
                workspace=workspace,
                servers=(MCPServerProfile(name='remote', transport='stdio', command='fake-mcp-server'),),
            )
            runtime._tool_cache_by_server['remote'] = (
                MCPTool(
                    name='tavily_search',
                    server_name='remote',
                    description='Search the web for current information and news.',
                    input_schema={
                        'type': 'object',
                        'properties': {'query': {'type': 'string'}},
                        'required': ['query'],
                    },
                ),
            )

            rendered = runtime.render_capability_index(server_name='remote')

        self.assertIn('- mcp:remote:tavily_search', rendered)
        self.assertIn('tool_name: tavily_search', rendered)
        self.assertIn('server: remote', rendered)
        self.assertIn('risk: read', rendered)
        self.assertIn('required: query', rendered)
        self.assertIn('parameters: query:string', rendered)



if __name__ == '__main__':
    unittest.main()