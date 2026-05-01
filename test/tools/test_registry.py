"""工具注册表操作单元测试。

使用 pytest 框架验证 build_registry、merge_tool_registries 与
render_openai_tools 三种纯粹函数的行为。
"""

from __future__ import annotations

import pytest

from core_contracts.tools_contracts import ToolDescriptor
from tools.registry_builder import (
    build_registry,
    merge_tool_registries,
    render_openai_tools,
)


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


# ── build_registry ──────────────────────────────────────────────────────────


class TestBuildRegistry:
    """验证 build_registry 将 ToolDescriptor 列表建立为名字典。"""

    def test_builds_dict_from_multiple_tools(self) -> None:
        a = _make_descriptor('tool_a')
        b = _make_descriptor('tool_b')
        registry = build_registry(a, b)
        assert isinstance(registry, dict)
        assert len(registry) == 2
        assert registry['tool_a'] is a
        assert registry['tool_b'] is b

    def test_builds_empty_dict_when_no_args(self) -> None:
        registry = build_registry()
        assert isinstance(registry, dict)
        assert len(registry) == 0

    def test_duplicates_by_last(self) -> None:
        a = _make_descriptor('same')
        b = _make_descriptor('same')
        registry = build_registry(a, b)
        assert len(registry) == 1
        assert registry['same'] is b


# ── merge_tool_registries ───────────────────────────────────────────────────


class TestMergeToolRegistries:
    """验证 merge_tool_registries 按顺序合并并采用后覆先策略。"""

    def test_merges_two_registries(self) -> None:
        r1 = build_registry(_make_descriptor('a'))
        r2 = build_registry(_make_descriptor('b'))
        merged = merge_tool_registries(r1, r2)
        assert len(merged) == 2
        assert 'a' in merged
        assert 'b' in merged

    def test_later_overrides_earlier(self) -> None:
        r1 = build_registry(_make_descriptor('same'))
        r2 = build_registry(_make_descriptor('same'))
        merged = merge_tool_registries(r1, r2)
        assert len(merged) == 1
        assert merged['same'] is r2['same']

    def test_merges_empty_registries(self) -> None:
        merged = merge_tool_registries()
        assert isinstance(merged, dict)
        assert len(merged) == 0

    def test_merges_with_empty_first(self) -> None:
        r = build_registry(_make_descriptor('x'))
        merged = merge_tool_registries({}, r)
        assert len(merged) == 1
        assert 'x' in merged


# ── render_openai_tools ─────────────────────────────────────────────────────


class TestRenderOpenaiTools:
    """验证 render_openai_tools 将注册表投影为 OpenAI 兼容 schema 列表。"""

    def test_renders_single_tool(self) -> None:
        registry = build_registry(_make_descriptor('my_tool'))
        result = render_openai_tools(registry)
        assert isinstance(result, list)
        assert len(result) == 1
        schema = result[0]
        assert schema['type'] == 'function'
        assert schema['function']['name'] == 'my_tool'
        assert schema['function']['description'] == 'my_tool tool'
        assert 'parameters' in schema['function']

    def test_renders_multiple_tools(self) -> None:
        registry = build_registry(
            _make_descriptor('t1'),
            _make_descriptor('t2'),
            _make_descriptor('t3'),
        )
        result = render_openai_tools(registry)
        assert len(result) == 3
        names = {s['function']['name'] for s in result}
        assert names == {'t1', 't2', 't3'}

    def test_renders_empty_registry(self) -> None:
        result = render_openai_tools({})
        assert isinstance(result, list)
        assert len(result) == 0

    def test_schema_has_required_structure(self) -> None:
        registry = build_registry(_make_descriptor('demo'))
        result = render_openai_tools(registry)
        schema = result[0]
        assert 'type' in schema
        assert 'function' in schema
        func = schema['function']
        assert 'name' in func
        assert 'description' in func
        assert 'parameters' in func
