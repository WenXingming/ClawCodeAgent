"""工具注册表操作单元测试。

使用 pytest 框架验证 ToolRegistry 的构建、合并与 schema 投影行为。
"""

from __future__ import annotations

from core_contracts.tools_contracts import ToolDescriptor, ToolRegistry


# ── Fixtures ────────────────────────────────────────────────────────────────


def _dummy_handler(_args, _ctx):
    return 'ok'


def _make_descriptor(name: str) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f'{name} tool',
        parameters={'type': 'object', 'properties': {'arg': {'type': 'string'}}},
        handler=_dummy_handler,
    )


# ── ToolRegistry 构建 ───────────────────────────────────────────────────────


class TestToolRegistryConstruction:
    """验证 ToolRegistry.from_tools 构建行为。"""

    def test_builds_dict_from_multiple_tools(self) -> None:
        a = _make_descriptor('tool_a')
        b = _make_descriptor('tool_b')
        registry = ToolRegistry.from_tools(a, b)
        assert isinstance(registry, ToolRegistry)
        assert len(registry) == 2
        assert registry['tool_a'] is a
        assert registry['tool_b'] is b

    def test_builds_empty_dict_when_no_args(self) -> None:
        registry = ToolRegistry.from_tools()
        assert isinstance(registry, ToolRegistry)
        assert len(registry) == 0

    def test_duplicates_by_last(self) -> None:
        a = _make_descriptor('same')
        b = _make_descriptor('same')
        registry = ToolRegistry.from_tools(a, b)
        assert len(registry) == 1
        assert registry['same'] is b


# ── ToolRegistry 合并 ───────────────────────────────────────────────────────


class TestToolRegistryMerge:
    """验证 ToolRegistry.merged_with 按顺序合并并采用后覆先策略。"""

    def test_merges_two_registries(self) -> None:
        r1 = ToolRegistry.from_tools(_make_descriptor('a'))
        r2 = ToolRegistry.from_tools(_make_descriptor('b'))
        merged = r1.merged_with(r2)
        assert len(merged) == 2
        assert 'a' in merged
        assert 'b' in merged

    def test_later_overrides_earlier(self) -> None:
        r1 = ToolRegistry.from_tools(_make_descriptor('same'))
        r2 = ToolRegistry.from_tools(_make_descriptor('same'))
        merged = r1.merged_with(r2)
        assert len(merged) == 1
        assert merged['same'] is r2['same']

    def test_merges_with_empty_first(self) -> None:
        r = ToolRegistry.from_tools(_make_descriptor('x'))
        merged = ToolRegistry.from_tools().merged_with(r)
        assert len(merged) == 1
        assert 'x' in merged


# ── ToolRegistry schema 投影 ────────────────────────────────────────────────


class TestRenderOpenaiTools:
    """验证 ToolRegistry.to_openai_tools 将注册表投影为 OpenAI 兼容 schema 列表。"""

    def test_renders_single_tool(self) -> None:
        registry = ToolRegistry.from_tools(_make_descriptor('my_tool'))
        result = registry.to_openai_tools()
        assert isinstance(result, list)
        assert len(result) == 1
        schema = result[0]
        assert schema['type'] == 'function'
        assert schema['function']['name'] == 'my_tool'
        assert schema['function']['description'] == 'my_tool tool'
        assert 'parameters' in schema['function']

    def test_renders_multiple_tools(self) -> None:
        registry = ToolRegistry.from_tools(
            _make_descriptor('t1'),
            _make_descriptor('t2'),
            _make_descriptor('t3'),
        )
        result = registry.to_openai_tools()
        assert len(result) == 3
        names = {s['function']['name'] for s in result}
        assert names == {'t1', 't2', 't3'}

    def test_renders_empty_registry(self) -> None:
        result = ToolRegistry.from_tools().to_openai_tools()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_schema_has_required_structure(self) -> None:
        registry = ToolRegistry.from_tools(_make_descriptor('demo'))
        result = registry.to_openai_tools()
        schema = result[0]
        assert 'type' in schema
        assert 'function' in schema
        func = schema['function']
        assert 'name' in func
        assert 'description' in func
        assert 'parameters' in func

    def test_tool_registry_public_to_openai_tools_method(self) -> None:
        registry = ToolRegistry.from_tools(_make_descriptor('demo'))
        result = registry.to_openai_tools()
        assert len(result) == 1
        assert result[0]['function']['name'] == 'demo'
