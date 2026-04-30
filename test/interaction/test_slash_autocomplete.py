"""slash 自动补全输入适配测试。"""

from __future__ import annotations

import io
import unittest

from core_contracts.interaction import SlashAutocompleteEntry
from interaction import SlashAutocompleteCatalog, SlashAutocompletePrompt


class SlashAutocompletePromptTests(unittest.TestCase):
    def test_completion_catalog_matches_slash_prefix(self) -> None:
        catalog = SlashAutocompleteCatalog(
            (
                SlashAutocompleteEntry(name='help', description='help'),
                SlashAutocompleteEntry(name='status', description='status'),
                SlashAutocompleteEntry(name='tools', description='tools'),
            )
        )

        matches = catalog.get_matches('/st')

        self.assertEqual(tuple(entry.name for entry in matches), ('status',))

    def test_completion_catalog_returns_all_entries_for_bare_slash(self) -> None:
        catalog = SlashAutocompleteCatalog(
            (
                SlashAutocompleteEntry(name='help', description='help'),
                SlashAutocompleteEntry(name='status', description='status'),
            )
        )

        matches = catalog.get_matches('/')

        self.assertEqual(tuple(entry.name for entry in matches), ('help', 'status'))

    def test_completion_catalog_does_not_complete_after_arguments(self) -> None:
        catalog = SlashAutocompleteCatalog(
            (
                SlashAutocompleteEntry(name='tools', description='tools'),
            )
        )

        self.assertEqual(catalog.get_matches('/tools verbose'), ())

    def test_slash_autocomplete_prompt_falls_back_to_builtin_input_when_stream_not_tty(self) -> None:
        calls: list[str] = []

        def _fallback(prompt_text: str) -> str:
            calls.append(prompt_text)
            return '/status'

        prompt_reader = SlashAutocompletePrompt(
            entries=(SlashAutocompleteEntry(name='status', description='status'),),
            fallback_reader=_fallback,
            stdin=io.StringIO(),
            stdout=io.StringIO(),
        )

        self.assertEqual(prompt_reader.read('agent> '), '/status')
        self.assertEqual(calls, ['agent> '])

    def test_format_prompt_message_styles_agent_prompt(self) -> None:
        prompt_reader = SlashAutocompletePrompt(
            entries=(SlashAutocompleteEntry(name='status', description='status'),),
            fallback_reader=lambda prompt_text: prompt_text,
            stdin=io.StringIO(),
            stdout=io.StringIO(),
        )

        fragments = prompt_reader._format_prompt_message('agent> ')

        self.assertEqual(list(fragments), [('class:prompt.label', 'agent'), ('class:prompt.chevron', '> ')])

    def test_build_prompt_style_contains_completion_palette(self) -> None:
        prompt_reader = SlashAutocompletePrompt(
            entries=(SlashAutocompleteEntry(name='status', description='status'),),
            fallback_reader=lambda prompt_text: prompt_text,
            stdin=io.StringIO(),
            stdout=io.StringIO(),
        )

        style = prompt_reader._build_prompt_style()

        self.assertIsNotNone(style)
        self.assertIn('completion-menu.completion.current', prompt_reader._PROMPT_STYLE_RULES)
        self.assertIn('scrollbar.arrow', prompt_reader._PROMPT_STYLE_RULES)
        self.assertIn('slash-autocomplete.command.current', prompt_reader._PROMPT_STYLE_RULES)
        self.assertIn('prompt.chevron', prompt_reader._PROMPT_STYLE_RULES)


if __name__ == '__main__':
    unittest.main()