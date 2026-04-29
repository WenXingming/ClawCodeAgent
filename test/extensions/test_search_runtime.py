"""ISSUE-020 WorkspaceGateway 搜索能力单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error

from workspace import WorkspaceGateway


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> '_FakeHTTPResponse':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class WorkspaceSearchGatewayTests(unittest.TestCase):
    def _write_manifest(self, workspace: Path, filename: str, payload: dict[str, object]) -> None:
        manifest_dir = workspace / '.claw' / 'search'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def test_from_workspace_discovers_manifest_and_env_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'workspace.json',
                {
                    'provider_id': 'workspace-search',
                    'provider': 'searxng',
                    'title': 'Workspace Search',
                    'base_url': 'http://127.0.0.1:8080',
                },
            )

            with patch.dict('os.environ', {'SEARXNG_BASE_URL': 'http://127.0.0.1:9090'}, clear=False):
                gateway = WorkspaceGateway.from_workspace(workspace)

            providers = gateway.list_search_providers()

        self.assertEqual([item['provider_id'] for item in providers], ['workspace-search', 'env-searxng'])
        self.assertEqual(gateway.search_provider_count, 2)

    def test_activate_provider_persists_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'primary.json',
                {
                    'provider_id': 'primary-search',
                    'provider': 'searxng',
                    'title': 'Primary Search',
                    'base_url': 'http://127.0.0.1:8080',
                },
            )
            self._write_manifest(
                workspace,
                'secondary.json',
                {
                    'provider_id': 'secondary-search',
                    'provider': 'searxng',
                    'title': 'Secondary Search',
                    'base_url': 'http://127.0.0.1:8081',
                },
            )

            gateway = WorkspaceGateway.from_workspace(workspace)
            activated = gateway.activate_search_provider('secondary-search')
            reloaded = WorkspaceGateway.from_workspace(workspace)
            persisted = json.loads((workspace / '.claw' / 'search_state.json').read_text(encoding='utf-8'))
            provider_ids = [item['provider_id'] for item in reloaded.list_search_providers()]

        self.assertEqual(activated['provider_id'], 'secondary-search')
        self.assertIn('secondary-search', provider_ids)
        self.assertEqual(persisted['active_provider_id'], 'secondary-search')

    @patch('urllib.request.urlopen')
    def test_search_returns_structured_results(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = _FakeHTTPResponse(
            {
                'results': [
                    {
                        'title': 'Alpha Result',
                        'url': 'https://example.com/alpha',
                        'content': 'alpha snippet',
                    },
                    {
                        'title': 'Beta Result',
                        'url': 'https://example.com/beta',
                        'content': 'beta snippet',
                    },
                ]
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'search.json',
                {
                    'provider_id': 'workspace-search',
                    'provider': 'searxng',
                    'title': 'Workspace Search',
                    'base_url': 'http://127.0.0.1:8080',
                    'default_max_results': 2,
                },
            )

            gateway = WorkspaceGateway.from_workspace(workspace)
            response = gateway.search('claw code agent')

        self.assertEqual(response['provider']['provider_id'], 'workspace-search')
        self.assertEqual(response['query'], 'claw code agent')
        self.assertEqual(response['attempts'], 1)
        self.assertEqual(len(response['results']), 2)
        self.assertEqual(response['results'][0]['title'], 'Alpha Result')
        self.assertEqual(response['results'][0]['provider_id'], 'workspace-search')
        self.assertEqual(response['results'][1]['rank'], 2)

    @patch('urllib.request.urlopen')
    def test_search_duckduckgo_returns_structured_results(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = _FakeHTTPResponse(
            {
                'Heading': 'ClawCodeAgent',
                'AbstractURL': 'https://example.com/overview',
                'AbstractText': 'overview snippet',
                'RelatedTopics': [
                    {
                        'FirstURL': 'https://example.com/topic-1',
                        'Text': 'Topic 1 - detail',
                    },
                    {
                        'Topics': [
                            {
                                'FirstURL': 'https://example.com/topic-2',
                                'Text': 'Topic 2 - detail',
                            }
                        ]
                    },
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'search.json',
                {
                    'provider_id': 'web-search',
                    'provider': 'duckduckgo',
                    'title': 'DuckDuckGo Search',
                },
            )

            gateway = WorkspaceGateway.from_workspace(workspace)
            response = gateway.search('claw code agent', max_results=2)

        self.assertEqual(response['provider']['provider_id'], 'web-search')
        self.assertEqual(response['results'][0]['title'], 'ClawCodeAgent')
        self.assertEqual(response['results'][0]['snippet'], 'overview snippet')
        self.assertEqual(response['results'][1]['title'], 'Topic 1')
        self.assertEqual(response['results'][1]['url'], 'https://example.com/topic-1')

    @patch('urllib.request.urlopen')
    def test_search_searxng_includes_api_key_headers_when_configured(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = _FakeHTTPResponse({'results': []})

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'search.json',
                {
                    'provider_id': 'workspace-search',
                    'provider': 'searxng',
                    'title': 'Workspace Search',
                    'base_url': 'http://127.0.0.1:8080',
                    'api_key_env': 'SEARXNG_API_KEY',
                },
            )

            with patch.dict('os.environ', {'SEARXNG_API_KEY': 'secret-key'}, clear=False):
                gateway = WorkspaceGateway.from_workspace(workspace)
                gateway.search('claw code agent')

        sent_request = mocked_urlopen.call_args[0][0]
        self.assertEqual(sent_request.headers.get('Authorization'), 'Bearer secret-key')
        self.assertEqual(sent_request.headers.get('X-api-key'), 'secret-key')

    @patch('urllib.request.urlopen')
    def test_search_duckduckgo_falls_back_to_http_when_https_fails(self, mocked_urlopen) -> None:
        def _side_effect(http_request, timeout=10):
            if str(http_request.full_url).startswith('https://'):
                raise error.URLError('ssl handshake failure')
            return _FakeHTTPResponse({'results': []})

        mocked_urlopen.side_effect = _side_effect

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'search.json',
                {
                    'provider_id': 'web-search',
                    'provider': 'duckduckgo',
                    'title': 'DuckDuckGo Search',
                    'base_url': 'https://api.duckduckgo.com',
                },
            )
            gateway = WorkspaceGateway.from_workspace(workspace)
            response = gateway.search('ClawCodeAgent')

        self.assertEqual(response['attempts'], 1)
        self.assertEqual(len(response['results']), 0)
        self.assertGreaterEqual(mocked_urlopen.call_count, 2)

    @patch('urllib.request.urlopen')
    def test_search_duckduckgo_weather_fallback_returns_weather_result(self, mocked_urlopen) -> None:
        def _side_effect(http_request, timeout=10):
            url = str(http_request.full_url)
            if 'duckduckgo.com' in url:
                return _FakeHTTPResponse({'Abstract': '', 'RelatedTopics': []})
            if 'wttr.in' in url:
                return _FakeHTTPResponse(
                    {
                        'current_condition': [
                            {
                                'temp_C': '26',
                                'FeelsLikeC': '28',
                                'humidity': '71',
                                'weatherDesc': [{'value': 'Partly cloudy'}],
                            }
                        ]
                    }
                )
            raise AssertionError(f'unexpected url: {url}')

        mocked_urlopen.side_effect = _side_effect

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'search.json',
                {
                    'provider_id': 'web-search',
                    'provider': 'duckduckgo',
                    'title': 'DuckDuckGo Search',
                    'base_url': 'https://api.duckduckgo.com',
                },
            )
            gateway = WorkspaceGateway.from_workspace(workspace)
            response = gateway.search('北京天气怎么样')

        self.assertEqual(len(response['results']), 1)
        self.assertEqual(response['results'][0]['title'], 'Weather in 北京')
        self.assertIn('Partly cloudy', response['results'][0]['snippet'])

    @patch('urllib.request.urlopen')
    def test_search_retries_failed_query_and_raises_controlled_error(self, mocked_urlopen) -> None:
        mocked_urlopen.side_effect = [
            error.URLError('temporary outage'),
            error.URLError('still down'),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            self._write_manifest(
                workspace,
                'search.json',
                {
                    'provider_id': 'workspace-search',
                    'provider': 'searxng',
                    'title': 'Workspace Search',
                    'base_url': 'http://127.0.0.1:8080',
                },
            )

            gateway = WorkspaceGateway.from_workspace(workspace)
            with self.assertRaisesRegex(ValueError, 'workspace-search'):
                gateway.search('claw code agent', max_retries=1)


if __name__ == '__main__':
    unittest.main()
