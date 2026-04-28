"""管理搜索 provider 的发现、激活、状态持久化与检索。

本模块负责从工作区和环境变量发现搜索 provider，维护当前激活的 provider 状态，并把不同后端的搜索调用统一封装为结构化 `SearchResponse`。
"""

from __future__ import annotations

import json
import os
import re
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
_DEFAULT_DUCKDUCKGO_BASE_URL = 'https://api.duckduckgo.com'
_DEFAULT_WTTR_BASE_URL = 'https://wttr.in'


@dataclass(frozen=True)
class SearchProviderProfile:
    """表示单个搜索 provider 配置。"""

    provider_id: str  # str：搜索 provider 的稳定唯一标识。
    provider: str  # str：后端类型，如 searxng 或 duckduckgo。
    title: str  # str：面向用户展示的 provider 名称。
    base_url: str  # str：当前 provider 的基础请求地址。
    description: str = ''  # str：provider 的补充说明。
    api_key_env: str | None = None  # str | None：保存 API Key 的环境变量名。
    default_max_results: int = 5  # int：未显式传参时的默认返回条数。
    source_path: Path | None = None  # Path | None：provider 定义来源文件路径。

    def to_dict(self) -> JSONDict:
        """把 provider 配置转换成 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前 provider 配置的可序列化字典表示。
        """
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
        """从 JSON 字典恢复 provider 配置对象。

        Args:
            payload (JSONDict | None): 待反序列化的原始字典。
            source_path (Path | None): 当前 provider 配置来源文件路径。
        Returns:
            SearchProviderProfile: 恢复后的 provider 配置对象。
        Raises:
            ValueError: 当 provider_id、provider、title 或 base_url 非法时抛出。
        """
        data = dict(payload or {})
        provider_id = _normalize_provider_id(data.get('provider_id', data.get('providerId', '')))
        provider = str(data.get('provider', '')).strip().lower()
        if not provider:
            raise ValueError(f'Search provider {provider_id!r} requires non-empty provider')

        title = str(data.get('title', '')).strip()
        if not title:
            raise ValueError(f'Search provider {provider_id!r} requires non-empty title')

        base_url = _normalize_base_url(
            data.get('base_url', data.get('baseUrl')),
            provider=provider,
        )
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

    provider_id: str  # str：加载失败的 provider ID 或文件 stem。
    error: str  # str：对应的错误说明文本。
    source_path: Path | None = None  # Path | None：出错的来源文件路径。


@dataclass(frozen=True)
class SearchResult:
    """单条结构化搜索结果。"""

    title: str  # str：搜索结果标题。
    url: str  # str：搜索结果链接。
    snippet: str  # str：搜索结果摘要文本。
    provider_id: str  # str：产生该结果的 provider 标识。
    rank: int  # int：该结果在本次返回中的排序序号。

    def to_dict(self) -> JSONDict:
        """把单条搜索结果转换成 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前搜索结果的可序列化字典表示。
        """
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

    provider: SearchProviderProfile  # SearchProviderProfile：本次搜索实际使用的 provider。
    query: str  # str：本次搜索的规范化查询文本。
    results: tuple[SearchResult, ...]  # tuple[SearchResult, ...]：本次搜索返回的结果集合。
    attempts: int  # int：本次搜索实际尝试的请求次数。

    def to_dict(self) -> JSONDict:
        """把搜索响应转换成 JSON 字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 当前搜索响应的可序列化字典表示。
        """
        return {
            'provider': self.provider.to_dict(),
            'query': self.query,
            'attempts': self.attempts,
            'results': [item.to_dict() for item in self.results],
        }


class SearchQueryError(RuntimeError):
    """搜索请求在重试后仍失败。"""

    def __init__(self, *, provider_id: str, query: str, attempts: int, last_error: str) -> None:
        """初始化搜索请求异常对象。

        Args:
            provider_id (str): 出错的 provider 标识。
            query (str): 本次搜索查询文本。
            attempts (int): 已尝试的请求次数。
            last_error (str): 最后一次失败的错误说明。
        Returns:
            None: 该方法初始化异常对象状态。
        """
        self.provider_id = provider_id
        self.query = query
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f'Search query failed for provider {provider_id!r} after {attempts} attempts: {last_error}'
        )


@dataclass
class SearchRuntime:
    """表示工作区本地搜索 provider 运行时。

    典型工作流如下：
    1. 调用 `from_workspace()` 发现并加载全部 provider 配置。
    2. 通过 `list_providers()`、`get_provider()` 和 `current_provider()` 读取当前 provider 状态。
    3. 调用 `activate_provider()` 更新当前激活 provider，并由 `search()` 执行统一的搜索入口。
    """

    workspace: Path  # Path：当前搜索运行时所属的工作区根目录。
    providers: tuple[SearchProviderProfile, ...] = ()  # tuple[SearchProviderProfile, ...]：已发现并加载成功的 provider 集合。
    active_provider_id: str | None = None  # str | None：当前工作区保存的激活 provider 标识。
    load_errors: tuple[SearchLoadError, ...] = ()  # tuple[SearchLoadError, ...]：加载 provider 过程中收集到的错误信息。
    schema_version: int = _SCHEMA_VERSION  # int：当前搜索状态文件使用的 schema 版本。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'SearchRuntime':
        """从工作区与环境变量加载搜索 provider 配置。

        Args:
            workspace (Path): 工作区根目录。

        Returns:
            SearchRuntime: 包含 provider 列表与加载错误的运行时对象。
        """
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
        """返回当前已加载的全部搜索 provider。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[SearchProviderProfile, ...]: 当前 provider 集合的只读视图。
        """
        return self.providers

    def get_provider(self, provider_id: str) -> SearchProviderProfile:
        """按 provider_id 获取单个搜索 provider。

        Args:
            provider_id (str): 需要查找的 provider 标识。
        Returns:
            SearchProviderProfile: 找到的 provider 配置对象。
        Raises:
            ValueError: 当 provider 不存在或 provider_id 非法时抛出。
        """
        normalized_provider_id = _normalize_provider_id(provider_id)
        for provider in self.providers:
            if provider.provider_id == normalized_provider_id:
                return provider
        raise ValueError(f'Unknown search provider: {normalized_provider_id!r}')

    def current_provider(self) -> SearchProviderProfile:
        """返回当前生效的搜索 provider。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            SearchProviderProfile: 当前激活或默认回退后的 provider 配置对象。
        Raises:
            ValueError: 当没有任何可用 provider 时抛出。
        """
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
        """激活指定 provider 并写回状态文件。

        Args:
            provider_id (str): 需要激活的 provider 标识。
        Returns:
            SearchProviderProfile: 被成功激活的 provider 配置对象。
        Raises:
            ValueError: 当 provider 不存在或 provider_id 非法时抛出。
        """
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
        """执行一次统一的搜索请求。

        Args:
            query (str): 本次搜索的原始查询文本。
            provider_id (str | None): 可选的 provider 标识；不传时使用当前激活 provider。
            max_results (int | None): 可选的最大返回结果数；不传时使用 provider 默认值。
            timeout_seconds (float): 单次请求超时时间，单位为秒。
            max_retries (int): 请求失败后的最大重试次数。
        Returns:
            SearchResponse: 本次搜索的结构化返回结果。
        Raises:
            ValueError: 当查询文本为空或 provider 非法时抛出。
            SearchQueryError: 当所有重试都失败后抛出。
        """
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
            except (error.HTTPError, error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
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
        """把当前激活 provider 状态写回工作区文件。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            Path: 实际写入的搜索状态文件路径。
        """
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
    """发现工作区中所有候选搜索 provider 清单文件路径。

    Args:
        workspace (Path): 工作区根目录。
    Returns:
        tuple[Path, ...]: 按稳定顺序返回的 provider 清单文件路径元组。
    """
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
    """从单个清单文件中加载一个或多个 provider 配置。

    Args:
        path (Path): 待读取的 provider 清单文件路径。
    Returns:
        tuple[tuple[SearchProviderProfile, ...], tuple[SearchLoadError, ...]]: 成功加载的 provider 集合与对应的加载错误集合。
    """
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
    """从环境变量发现额外的搜索 provider。

    Args:
        None: 该函数不接收额外参数。
    Returns:
        tuple[SearchProviderProfile, ...]: 从环境变量派生出的 provider 集合。
    """
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
    """从工作区状态文件中读取当前激活的 provider_id。

    Args:
        workspace (Path): 工作区根目录。
    Returns:
        str | None: 当前已保存的激活 provider 标识；未保存时返回 None。
    Raises:
        ValueError: 当状态文件结构非法时抛出。
    """
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
    """根据 provider 后端类型分发到具体搜索实现。

    Args:
        provider (SearchProviderProfile): 当前执行搜索的 provider 配置。
        query (str): 已规范化的查询文本。
        max_results (int): 当前最多返回的结果数量。
        timeout_seconds (float): 单次请求超时时间，单位为秒。
    Returns:
        tuple[SearchResult, ...]: 当前 provider 返回的搜索结果集合。
    Raises:
        ValueError: 当 provider 后端类型不受支持时抛出。
        OSError: 当底层网络请求失败时按现有逻辑透传。
    """
    if provider.provider == 'searxng':
        return _search_searxng(provider, query, max_results=max_results, timeout_seconds=timeout_seconds)
    if provider.provider == 'duckduckgo':
        return _search_duckduckgo(provider, query, max_results=max_results, timeout_seconds=timeout_seconds)
    raise ValueError(f'Unsupported search provider backend: {provider.provider!r}')


def _search_searxng(
    provider: SearchProviderProfile,
    query: str,
    *,
    max_results: int,
    timeout_seconds: float,
) -> tuple[SearchResult, ...]:
    """调用 Searxng 后端执行搜索并标准化结果。

    Args:
        provider (SearchProviderProfile): 当前执行搜索的 provider 配置。
        query (str): 已规范化的查询文本。
        max_results (int): 当前最多返回的结果数量。
        timeout_seconds (float): 单次请求超时时间，单位为秒。
    Returns:
        tuple[SearchResult, ...]: 标准化后的搜索结果集合。
    """
    endpoint = provider.base_url.rstrip('/')
    if not endpoint.endswith('/search'):
        endpoint += '/search'

    url = endpoint + '?' + parse.urlencode({'q': query, 'format': 'json'})
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'claw-code-agent/1.0',
    }
    api_key = _resolve_provider_api_key(provider)
    if api_key is not None:
        headers['Authorization'] = f'Bearer {api_key}'
        headers['X-API-Key'] = api_key

    http_request = request.Request(
        url,
        headers=headers,
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


def _search_duckduckgo(
    provider: SearchProviderProfile,
    query: str,
    *,
    max_results: int,
    timeout_seconds: float,
) -> tuple[SearchResult, ...]:
    """调用 DuckDuckGo 后端执行搜索并标准化结果。

    Args:
        provider (SearchProviderProfile): 当前执行搜索的 provider 配置。
        query (str): 已规范化的查询文本。
        max_results (int): 当前最多返回的结果数量。
        timeout_seconds (float): 单次请求超时时间，单位为秒。
    Returns:
        tuple[SearchResult, ...]: 标准化后的搜索结果集合，必要时会回退到天气查询兜底逻辑。
    """
    payload = _query_duckduckgo_payload(query, provider.base_url, timeout_seconds=timeout_seconds)

    if not isinstance(payload, dict):
        return ()

    rendered_results: list[SearchResult] = []
    ranked: list[tuple[str, str]] = []

    abstract_url = _normalize_optional_text(payload.get('AbstractURL'))
    abstract_text = _normalize_optional_text(payload.get('AbstractText'))
    abstract_title = _normalize_optional_text(payload.get('Heading'))
    if abstract_url is not None:
        ranked.append((abstract_title or abstract_url, abstract_url))

    related_topics = payload.get('RelatedTopics')
    if isinstance(related_topics, list):
        ranked.extend(_collect_duckduckgo_topics(related_topics))

    for index, (title, url_value) in enumerate(ranked[:max_results], start=1):
        snippet = abstract_text if index == 1 and abstract_url is not None and abstract_text is not None else ''
        rendered_results.append(
            SearchResult(
                title=title,
                url=url_value,
                snippet=snippet,
                provider_id=provider.provider_id,
                rank=index,
            )
        )

    if rendered_results:
        return tuple(rendered_results)

    if _looks_like_weather_query(query):
        weather_result = _search_weather_fallback(query, provider.provider_id, timeout_seconds=timeout_seconds)
        if weather_result is not None:
            return (weather_result,)

    return tuple(rendered_results)


def _query_duckduckgo_payload(query: str, base_url: str, *, timeout_seconds: float) -> JSONDict:
    """内部方法：查询 DuckDuckGo，并在 HTTPS 不可用时回退到 HTTP。"""
    endpoint = base_url.rstrip('/')
    candidates: list[str] = [endpoint]
    if endpoint.startswith('https://'):
        candidates.append('http://' + endpoint[len('https://'):])

    params = {
        'q': query,
        'format': 'json',
        'no_html': '1',
        'no_redirect': '1',
        'skip_disambig': '1',
    }
    last_error: BaseException | None = None
    for candidate in candidates:
        url = candidate + '?' + parse.urlencode(params)
        http_request = request.Request(
            url,
            headers={
                'Accept': 'application/json',
                'User-Agent': 'claw-code-agent/1.0',
            },
        )
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode('utf-8', errors='replace'))
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
        if isinstance(payload, dict):
            return payload
        last_error = ValueError('DuckDuckGo response must be a JSON object')

    if last_error is None:
        raise ValueError('DuckDuckGo query failed with unknown error')
    raise last_error


def _looks_like_weather_query(query: str) -> bool:
    """内部方法：判断查询是否为天气类问题。"""
    lowered = query.lower()
    keywords = ('weather', 'temperature', 'forecast', '天气', '气温', '温度', '预报')
    return any(item in lowered for item in keywords)


def _search_weather_fallback(query: str, provider_id: str, *, timeout_seconds: float) -> SearchResult | None:
    """内部方法：天气查询兜底，调用 wttr.in 获取概览。"""
    location = _extract_weather_location(query)
    endpoint = f"{_DEFAULT_WTTR_BASE_URL}/{parse.quote(location)}?format=j1"
    http_request = request.Request(
        endpoint,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'claw-code-agent/1.0',
        },
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode('utf-8', errors='replace'))
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    current_condition = payload.get('current_condition')
    if not isinstance(current_condition, list) or not current_condition:
        return None
    first = current_condition[0]
    if not isinstance(first, dict):
        return None

    temp_c = _normalize_optional_text(first.get('temp_C')) or '?'
    feels_like_c = _normalize_optional_text(first.get('FeelsLikeC')) or '?'
    humidity = _normalize_optional_text(first.get('humidity')) or '?'
    weather_desc = ''
    desc_list = first.get('weatherDesc')
    if isinstance(desc_list, list) and desc_list and isinstance(desc_list[0], dict):
        weather_desc = _normalize_optional_text(desc_list[0].get('value')) or ''
    snippet_parts = [f'temp {temp_c}C', f'feels {feels_like_c}C', f'humidity {humidity}%']
    if weather_desc:
        snippet_parts.insert(0, weather_desc)

    return SearchResult(
        title=f'Weather in {location}',
        url=f'https://wttr.in/{parse.quote(location)}',
        snippet='; '.join(snippet_parts),
        provider_id=provider_id,
        rank=1,
    )


def _extract_weather_location(query: str) -> str:
    """内部方法：从查询中提取天气地点，提取失败时回退为当前地点。"""
    normalized = query.strip()
    if not normalized:
        return 'Beijing'

    lowered = normalized.lower()
    prefixes = ('weather in ', 'weather at ', 'forecast for ')
    for prefix in prefixes:
        if lowered.startswith(prefix):
            candidate = normalized[len(prefix):].strip(' ?!,.，。')
            if candidate:
                return candidate

    for token in ('天气', 'weather', '气温', '温度', '预报', 'forecast', 'temperature'):
        normalized = normalized.replace(token, ' ')

    filler_tokens = (
        '怎么样',
        '如何',
        '多少度',
        '多少',
        '大概',
        '今天',
        '明天',
        '后天',
        '现在',
        '吗',
        '呢',
        'what is',
        'how is',
        'please',
    )
    for token in filler_tokens:
        normalized = normalized.replace(token, ' ')

    candidate = ' '.join(normalized.split()).strip(' ?!,.，。')
    if _contains_cjk(candidate):
        candidate = re.split(r'\s+', candidate, maxsplit=1)[0].strip(' ?!,.，。')
    return candidate or 'Beijing'


def _contains_cjk(text: str) -> bool:
    """内部方法：判断文本是否包含 CJK 字符。"""
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def _collect_duckduckgo_topics(topics: list[object]) -> list[tuple[str, str]]:
    """内部方法：从 DuckDuckGo RelatedTopics 扁平化 URL 与标题。"""
    collected: list[tuple[str, str]] = []
    for item in topics:
        if not isinstance(item, dict):
            continue

        first_url = _normalize_optional_text(item.get('FirstURL'))
        text = _normalize_optional_text(item.get('Text'))
        if first_url is not None:
            title = text.split(' - ', 1)[0] if text is not None else first_url
            collected.append((title, first_url))
            continue

        nested_topics = item.get('Topics')
        if isinstance(nested_topics, list):
            collected.extend(_collect_duckduckgo_topics(nested_topics))
    return collected


def _normalize_provider_id(value: object) -> str:
    """规范化并校验 provider_id。

    Args:
        value (object): 待校验的原始 provider_id。
    Returns:
        str: 去除首尾空白后的合法 provider_id。
    Raises:
        ValueError: 当 provider_id 不是字符串、为空或包含非法路径成分时抛出。
    """
    if not isinstance(value, str):
        raise ValueError('provider_id must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError('provider_id must not be empty')
    if normalized in {'.', '..'} or any(separator in normalized for separator in ('/', '\\')):
        raise ValueError(f'Invalid provider_id: {value!r}')
    return normalized


def _normalize_base_url(value: object, *, provider: str) -> str:
    """规范化 provider 的基础 URL，并在缺失时给出默认值。

    Args:
        value (object): 待校验的原始 base_url。
        provider (str): 当前 provider 后端类型，用于决定默认 URL。
    Returns:
        str: 规范化后的 base_url。
    Raises:
        ValueError: 当提供的 URL 不是合法的绝对 URL 时抛出。
    """
    normalized = _normalize_optional_text(value)
    if normalized is None:
        if provider == 'duckduckgo':
            return _DEFAULT_DUCKDUCKGO_BASE_URL
        return _DEFAULT_SEARXNG_BASE_URL
    if '://' not in normalized:
        raise ValueError(f'Invalid base_url: {value!r}')
    return normalized.rstrip('/')


def _resolve_provider_api_key(provider: SearchProviderProfile) -> str | None:
    """内部方法：从 provider 配置解析 API Key。"""
    env_name = provider.api_key_env
    if env_name is None:
        return None
    api_key = _normalize_optional_text(os.environ.get(env_name))
    return api_key


def _normalize_optional_text(value: object) -> str | None:
    """把可选文本输入规范化为字符串或 None。

    Args:
        value (object): 待规范化的原始输入值。
    Returns:
        str | None: 去空白后的字符串；若为空则返回 None。
    """
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _coerce_positive_int(value: object, default: int) -> int:
    """把输入值安全转换为正整数。

    Args:
        value (object): 待转换的原始值。
        default (int): 输入无效或非正数时使用的默认值。
    Returns:
        int: 转换后的正整数；失败时返回默认值。
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _format_request_error(exc: BaseException) -> str:
    """把底层请求异常格式化为简短可读的错误文本。

    Args:
        exc (BaseException): 底层请求或解析阶段抛出的异常对象。
    Returns:
        str: 适合写入重试错误聚合信息的人类可读文本。
    """
    if isinstance(exc, error.HTTPError):
        body = ''
        try:
            body = exc.read().decode('utf-8', errors='replace').strip()
        except Exception:
            body = ''
        if body:
            body = body.replace('\n', ' ')[:200]
            return f'HTTP {exc.code} {exc.reason}: {body}'
        return f'HTTP {exc.code} {exc.reason}'

    reason = getattr(exc, 'reason', None)
    if reason is not None:
        return str(reason)
    return str(exc)