"""ISSUE-020 Search Runtime：provider 发现、激活与检索。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, parse, request

from core_contracts.protocol import JSONDict


_SEARCH_MANIFEST_FILE = Path('.claw') / 'search.json'
_SEARCH_MANIFEST_DIR = Path('.claw') / 'search'
_SEARCH_STATE_FILE = Path('.claw') / 'search_state.json'
_SCHEMA_VERSION = 1
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_SEARXNG_BASE_URL = 'http://127.0.0.1:8080'


@dataclass(frozen=True)
class SearchProviderProfile:
    """单个搜索 provider profile。"""

    provider_id: str
    provider: str
    title: str
    base_url: str
    description: str = ''
    api_key_env: str | None = None
    default_max_results: int = 5
    source_path: Path | None = None

    def to_dict(self) -> JSONDict:
        payload: JSONDict = {
            'provider_id': self.provider_id,
            'provider': self.provider,
            'title': self.title,
            'base_url': self.base_url,
            'description': self.description,
            'default_max_results': self.default_max_results,
        }
        if self.api_key_env is not None:
            payload['api_key_env'] = self.api_key_env
        if self.source_path is not None:
            payload['source_path'] = str(self.source_path)
        return payload

    @classmethod
    def from_dict(cls, payload: JSONDict | None, *, source_path: Path | None = None) -> 'SearchProviderProfile':
        data = dict(payload or {})
        provider_id = _normalize_provider_id(data.get('provider_id', data.get('providerId', '')))
        provider = str(data.get('provider', '')).strip().lower()
        if not provider:
            raise ValueError(f'Search provider {provider_id!r} requires non-empty provider')

        title = str(data.get('title', '')).strip()
        if not title:
            raise ValueError(f'Search provider {provider_id!r} requires non-empty title')

        base_url = _normalize_base_url(data.get('base_url', data.get('baseUrl')))
        default_max_results = _coerce_positive_int(data.get('default_max_results', data.get('defaultMaxResults')), 5)

        return cls(
            provider_id=provider_id,
            provider=provider,
            title=title,
            base_url=base_url,
            description=str(data.get('description', '')).strip(),
            api_key_env=_normalize_optional_text(data.get('api_key_env', data.get('apiKeyEnv'))),
            default_max_results=default_max_results,
            source_path=source_path.resolve() if source_path is not None else None,
        )


@dataclass(frozen=True)
class SearchLoadError:
    """provider manifest 加载错误。"""

    provider_id: str
    error: str
    source_path: Path | None = None


@dataclass(frozen=True)
class SearchResult:
    """单条结构化搜索结果。"""

    title: str
    url: str
    snippet: str
    provider_id: str
    rank: int

    def to_dict(self) -> JSONDict:
        return {
            'title': self.title,
            'url': self.url,
            'snippet': self.snippet,
            'provider_id': self.provider_id,
            'rank': self.rank,
        }


@dataclass(frozen=True)
class SearchResponse:
    """一次搜索调用的结构化返回。"""

    provider: SearchProviderProfile
    query: str
    results: tuple[SearchResult, ...]
    attempts: int

    def to_dict(self) -> JSONDict:
        return {
            'provider': self.provider.to_dict(),
            'query': self.query,
            'attempts': self.attempts,
            'results': [item.to_dict() for item in self.results],
        }


class SearchQueryError(RuntimeError):
    """搜索请求在重试后仍失败。"""

    def __init__(self, *, provider_id: str, query: str, attempts: int, last_error: str) -> None:
        self.provider_id = provider_id
        self.query = query
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f'Search query failed for provider {provider_id!r} after {attempts} attempts: {last_error}'
        )


@dataclass
class SearchRuntime:
    """工作区本地 Search Runtime。"""

    workspace: Path
    providers: tuple[SearchProviderProfile, ...] = ()
    active_provider_id: str | None = None
    load_errors: tuple[SearchLoadError, ...] = ()
    schema_version: int = _SCHEMA_VERSION

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'SearchRuntime':
        resolved_workspace = workspace.resolve()
        providers: list[SearchProviderProfile] = []
        load_errors: list[SearchLoadError] = []
        seen_provider_ids: set[str] = set()

        for manifest_path in _discover_manifest_paths(resolved_workspace):
            manifest_providers, manifest_errors = _load_manifest_providers(manifest_path)
            for load_error in manifest_errors:
                load_errors.append(load_error)
            for provider in manifest_providers:
                if provider.provider_id in seen_provider_ids:
                    load_errors.append(
                        SearchLoadError(
                            provider_id=provider.provider_id,
                            error=f'Duplicate search provider id: {provider.provider_id}',
                            source_path=provider.source_path,
                        )
                    )
                    continue
                seen_provider_ids.add(provider.provider_id)
                providers.append(provider)

        for provider in _load_env_providers():
            if provider.provider_id in seen_provider_ids:
                continue
            seen_provider_ids.add(provider.provider_id)
            providers.append(provider)

        active_provider_id = _load_active_provider_id(resolved_workspace)
        return cls(
            workspace=resolved_workspace,
            providers=tuple(providers),
            active_provider_id=active_provider_id,
            load_errors=tuple(load_errors),
        )

    def list_providers(self) -> tuple[SearchProviderProfile, ...]:
        return self.providers

    def get_provider(self, provider_id: str) -> SearchProviderProfile:
        normalized_provider_id = _normalize_provider_id(provider_id)
        for provider in self.providers:
            if provider.provider_id == normalized_provider_id:
                return provider
        raise ValueError(f'Unknown search provider: {normalized_provider_id!r}')

    def current_provider(self) -> SearchProviderProfile:
        if self.active_provider_id is not None:
            try:
                return self.get_provider(self.active_provider_id)
            except ValueError:
                pass

        env_default = _normalize_optional_text(os.environ.get('CLAW_SEARCH_PROVIDER'))
        if env_default is not None:
            try:
                return self.get_provider(env_default)
            except ValueError:
                pass

        if not self.providers:
            raise ValueError('No search providers are configured')
        return self.providers[0]

    def activate_provider(self, provider_id: str) -> SearchProviderProfile:
        provider = self.get_provider(provider_id)
        self.active_provider_id = provider.provider_id
        self._save_state()
        return provider

    def search(
        self,
        query: str,
        *,
        provider_id: str | None = None,
        max_results: int | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 0,
    ) -> SearchResponse:
        normalized_query = str(query).strip()
        if not normalized_query:
            raise ValueError('search query must not be empty')

        provider = self.get_provider(provider_id) if provider_id is not None else self.current_provider()
        effective_max_results = _coerce_positive_int(max_results, provider.default_max_results)
        attempts = 0
        error_messages: list[str] = []

        for _ in range(max_retries + 1):
            attempts += 1
            try:
                results = _search_with_provider(
                    provider,
                    normalized_query,
                    max_results=effective_max_results,
                    timeout_seconds=timeout_seconds,
                )
            except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
                error_messages.append(_format_request_error(exc))
                continue

            return SearchResponse(
                provider=provider,
                query=normalized_query,
                results=results,
                attempts=attempts,
            )

        raise SearchQueryError(
            provider_id=provider.provider_id,
            query=normalized_query,
            attempts=attempts,
            last_error='; '.join(error_messages) if error_messages else 'unknown search failure',
        )

    def _save_state(self) -> Path:
        path = self.workspace / _SEARCH_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    'schema_version': self.schema_version,
                    'active_provider_id': self.active_provider_id,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        return path


def _discover_manifest_paths(workspace: Path) -> tuple[Path, ...]:
    discovered: list[Path] = []
    single_manifest = workspace / _SEARCH_MANIFEST_FILE
    if single_manifest.is_file():
        discovered.append(single_manifest.resolve())

    manifest_dir = workspace / _SEARCH_MANIFEST_DIR
    if manifest_dir.is_dir():
        discovered.extend(
            path.resolve()
            for path in sorted(manifest_dir.glob('*.json'))
            if path.is_file()
        )
    return tuple(discovered)


def _load_manifest_providers(path: Path) -> tuple[tuple[SearchProviderProfile, ...], tuple[SearchLoadError, ...]]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return (), (SearchLoadError(provider_id=path.stem, error=str(exc), source_path=path),)

    providers_payload: list[JSONDict] = []
    if isinstance(payload, dict) and isinstance(payload.get('providers'), list):
        providers_payload = [item for item in payload['providers'] if isinstance(item, dict)]
    elif isinstance(payload, dict):
        providers_payload = [payload]
    else:
        return (), (SearchLoadError(provider_id=path.stem, error='Search manifest must be a JSON object', source_path=path),)

    providers: list[SearchProviderProfile] = []
    load_errors: list[SearchLoadError] = []
    for item in providers_payload:
        try:
            provider = SearchProviderProfile.from_dict(item, source_path=path)
        except ValueError as exc:
            provider_id = str(item.get('provider_id', item.get('providerId', path.stem)))
            load_errors.append(SearchLoadError(provider_id=provider_id, error=str(exc), source_path=path))
            continue
        providers.append(provider)
    return tuple(providers), tuple(load_errors)


def _load_env_providers() -> tuple[SearchProviderProfile, ...]:
    providers: list[SearchProviderProfile] = []
    searxng_base_url = _normalize_optional_text(os.environ.get('SEARXNG_BASE_URL'))
    if searxng_base_url is not None:
        providers.append(
            SearchProviderProfile(
                provider_id='env-searxng',
                provider='searxng',
                title='Environment Searxng',
                base_url=searxng_base_url,
                description='Discovered from SEARXNG_BASE_URL.',
            )
        )
    return tuple(providers)


def _load_active_provider_id(workspace: Path) -> str | None:
    state_path = workspace / _SEARCH_STATE_FILE
    if not state_path.is_file():
        return None

    payload = json.loads(state_path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'Search state file {state_path} must contain a JSON object')

    return _normalize_optional_text(payload.get('active_provider_id', payload.get('activeProviderId')))


def _search_with_provider(
    provider: SearchProviderProfile,
    query: str,
    *,
    max_results: int,
    timeout_seconds: float,
) -> tuple[SearchResult, ...]:
    if provider.provider == 'searxng':
        return _search_searxng(provider, query, max_results=max_results, timeout_seconds=timeout_seconds)
    raise ValueError(f'Unsupported search provider backend: {provider.provider!r}')


def _search_searxng(
    provider: SearchProviderProfile,
    query: str,
    *,
    max_results: int,
    timeout_seconds: float,
) -> tuple[SearchResult, ...]:
    endpoint = provider.base_url.rstrip('/')
    if not endpoint.endswith('/search'):
        endpoint += '/search'

    url = endpoint + '?' + parse.urlencode({'q': query, 'format': 'json'})
    http_request = request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'claw-code-agent/1.0',
        },
    )
    with request.urlopen(http_request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode('utf-8', errors='replace'))

    results_raw = payload.get('results') if isinstance(payload, dict) else None
    if not isinstance(results_raw, list):
        return ()

    rendered_results: list[SearchResult] = []
    for index, item in enumerate(results_raw[:max_results], start=1):
        if not isinstance(item, dict):
            continue
        url_value = _normalize_optional_text(item.get('url'))
        if url_value is None:
            continue
        title = _normalize_optional_text(item.get('title')) or url_value
        snippet = _normalize_optional_text(item.get('content')) or _normalize_optional_text(item.get('snippet')) or ''
        rendered_results.append(
            SearchResult(
                title=title,
                url=url_value,
                snippet=snippet,
                provider_id=provider.provider_id,
                rank=index,
            )
        )
    return tuple(rendered_results)


def _normalize_provider_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('provider_id must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError('provider_id must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid provider_id: {value!r}')
    return normalized


def _normalize_base_url(value: object) -> str:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return _DEFAULT_SEARXNG_BASE_URL
    if '://' not in normalized:
        raise ValueError(f'Invalid base_url: {value!r}')
    return normalized.rstrip('/')


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _coerce_positive_int(value: object, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _format_request_error(exc: BaseException) -> str:
    reason = getattr(exc, 'reason', None)
    if reason is not None:
        return str(reason)
    return str(exc)