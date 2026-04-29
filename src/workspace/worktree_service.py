"""管理受管 git worktree 的进入、退出与历史持久化。

本模块负责在工作区范围内维护单个“当前激活”的 git worktree，涵盖仓库根目录与 git common dir 探测、enter/exit managed worktree 的稳定状态切换，以及 `.claw/worktree_state.json` 与 `.claw/worktree_history.json` 的持久化。

该运行时目前保持为独立 runtime，不直接接入主循环或 CLI 控制面；上层只需要消费 `current_cwd`、`active_worktree()` 与历史记录即可完成后续集成。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from core_contracts.primitives import JSONDict


_WORKTREE_STATE_FILE = Path('.claw') / 'worktree_state.json'
_WORKTREE_HISTORY_FILE = Path('.claw') / 'worktree_history.json'
_SCHEMA_VERSION = 1
_PATH_TOKEN_PATTERN = re.compile(r'[^A-Za-z0-9._-]+')


class WorktreeStatus(StrEnum):
    """受管工作树记录的稳定状态集合。"""

    ACTIVE = 'active'
    EXITED = 'exited'
    REMOVED = 'removed'


class WorktreeHistoryAction(StrEnum):
    """工作树运行历史中支持的动作集合。"""

    ENTER = 'enter'
    EXIT_KEEP = 'exit_keep'
    EXIT_REMOVE = 'exit_remove'


@dataclass(frozen=True)
class ManagedWorktreeRecord:
    """描述单个受管工作树的稳定状态。

    该对象既用于内存中的当前状态，也用于写入 `.claw/worktree_state.json`。
    上层可通过 `status` 判断当前记录是激活中、已退出保留，还是已从磁盘移除。
    """

    worktree_id: str  # str: 当前受管工作树的稳定标识。
    branch: str  # str: 该工作树对应的 git 分支名。
    path: Path  # Path: 工作树工作目录的绝对路径。
    repo_root: Path  # Path: 当前仓库顶层目录的绝对路径。
    git_common_dir: Path  # Path: 仓库共享 git common dir 的绝对路径。
    previous_cwd: Path  # Path: enter 前的逻辑 cwd，用于 exit 时回退。
    status: WorktreeStatus = WorktreeStatus.ACTIVE  # WorktreeStatus: 当前工作树生命周期状态。
    base_ref: str = 'HEAD'  # str: 创建该工作树时使用的 base ref。
    created_at: str = ''  # str: 进入该工作树的 UTC ISO 时间戳。
    updated_at: str = ''  # str: 最近一次状态变化的 UTC ISO 时间戳。

    def to_dict(self) -> JSONDict:
        """把受管工作树记录转换为可持久化字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 适合写入 JSON 文件的结构化对象。
        """
        return {
            'worktree_id': self.worktree_id,
            'branch': self.branch,
            'path': str(self.path),
            'repo_root': str(self.repo_root),
            'git_common_dir': str(self.git_common_dir),
            'previous_cwd': str(self.previous_cwd),
            'status': self.status.value,
            'base_ref': self.base_ref,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'ManagedWorktreeRecord':
        """从持久化字典恢复受管工作树记录。

        Args:
            payload (JSONDict | None): JSON 反序列化后的原始对象。
        Returns:
            ManagedWorktreeRecord: 解析并校验后的记录对象。
        Raises:
            ValueError: 当关键字段缺失或非法时抛出。
        """
        data = dict(payload or {})
        worktree_id = _normalize_identifier(data.get('worktree_id', data.get('worktreeId', '')), label='worktree_id')
        branch = str(data.get('branch', '')).strip()
        if not branch:
            raise ValueError(f'Managed worktree {worktree_id!r} requires non-empty branch')

        return cls(
            worktree_id=worktree_id,
            branch=branch,
            path=_coerce_path(data.get('path'), label='path'),
            repo_root=_coerce_path(data.get('repo_root', data.get('repoRoot')), label='repo_root'),
            git_common_dir=_coerce_path(
                data.get('git_common_dir', data.get('gitCommonDir')),
                label='git_common_dir',
            ),
            previous_cwd=_coerce_path(
                data.get('previous_cwd', data.get('previousCwd')),
                label='previous_cwd',
            ),
            status=WorktreeStatus(str(data.get('status', WorktreeStatus.ACTIVE.value)).strip()),
            base_ref=str(data.get('base_ref', data.get('baseRef', 'HEAD'))).strip() or 'HEAD',
            created_at=str(data.get('created_at', data.get('createdAt', ''))).strip(),
            updated_at=str(data.get('updated_at', data.get('updatedAt', ''))).strip(),
        )


@dataclass(frozen=True)
class WorktreeHistoryRecord:
    """描述一次 enter/exit 动作的持久化历史事件。"""

    event_id: str  # str: 历史事件唯一标识。
    action: WorktreeHistoryAction  # WorktreeHistoryAction: 本次历史事件动作类型。
    worktree_id: str  # str: 关联的受管工作树标识。
    branch: str  # str: 关联工作树的分支名。
    path: Path  # Path: 关联工作树路径。
    cwd_before: Path  # Path: 动作发生前的逻辑 cwd。
    cwd_after: Path  # Path: 动作完成后的逻辑 cwd。
    recorded_at: str  # str: 本次历史事件的 UTC ISO 时间戳。
    message: str = ''  # str: 面向诊断的简短说明。

    def to_dict(self) -> JSONDict:
        """把历史事件转换为可持久化字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 适合写入 JSON 的结构化对象。
        """
        return {
            'event_id': self.event_id,
            'action': self.action.value,
            'worktree_id': self.worktree_id,
            'branch': self.branch,
            'path': str(self.path),
            'cwd_before': str(self.cwd_before),
            'cwd_after': str(self.cwd_after),
            'recorded_at': self.recorded_at,
            'message': self.message,
        }

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'WorktreeHistoryRecord':
        """从字典恢复历史事件。

        Args:
            payload (JSONDict | None): JSON 反序列化后的原始对象。
        Returns:
            WorktreeHistoryRecord: 解析并校验后的历史记录对象。
        Raises:
            ValueError: 当关键字段缺失或非法时抛出。
        """
        data = dict(payload or {})
        return cls(
            event_id=_normalize_identifier(data.get('event_id', data.get('eventId', '')), label='event_id'),
            action=WorktreeHistoryAction(str(data.get('action', '')).strip()),
            worktree_id=_normalize_identifier(
                data.get('worktree_id', data.get('worktreeId', '')),
                label='worktree_id',
            ),
            branch=str(data.get('branch', '')).strip(),
            path=_coerce_path(data.get('path'), label='path'),
            cwd_before=_coerce_path(data.get('cwd_before', data.get('cwdBefore')), label='cwd_before'),
            cwd_after=_coerce_path(data.get('cwd_after', data.get('cwdAfter')), label='cwd_after'),
            recorded_at=str(data.get('recorded_at', data.get('recordedAt', ''))).strip(),
            message=str(data.get('message', '')).strip(),
        )


@dataclass
class WorktreeService:
    """工作区本地 worktree 运行时。

    当前实现只维护一个“激活中的受管工作树”，同时保留完整历史和已退出记录。
    外部典型调用流为：
    1. `from_workspace()` 解析仓库与历史状态。
    2. `enter_worktree()` 创建分支和 worktree，并把 `current_cwd` 切到目标目录。
    3. `exit_worktree(remove=...)` 回退 cwd，并决定保留或删除底层 worktree 目录。
    """

    workspace: Path  # Path: 当前工作区根目录，也是 `.claw` 状态文件的落点。
    repo_root: Path  # Path: 当前 git 仓库顶层目录。
    git_common_dir: Path  # Path: 当前仓库共享 git common dir。
    current_cwd: Path  # Path: 运行时逻辑上的当前 cwd，供上层控制面消费。
    managed_worktrees: tuple[ManagedWorktreeRecord, ...] = ()  # tuple[ManagedWorktreeRecord, ...]: 全部受管工作树记录。
    history_records: tuple[WorktreeHistoryRecord, ...] = ()  # tuple[WorktreeHistoryRecord, ...]: enter/exit 历史事件列表。
    schema_version: int = _SCHEMA_VERSION  # int: 当前状态文件 schema 版本号。

    @classmethod
    def from_workspace(cls, workspace: Path) -> 'WorktreeService':
        """从工作区加载 worktree runtime。

        Args:
            workspace (Path): 工作区根目录。
        Returns:
            WorktreeService: 解析并校验后的工作区 worktree 服务对象。
        Raises:
            ValueError: 当工作区不在 git 仓库内，或持久化状态与当前仓库不一致时抛出。
        """
        resolved_workspace = workspace.resolve()
        repo_root = _detect_repo_root(resolved_workspace)
        git_common_dir = _detect_git_common_dir(repo_root)
        state_payload = _load_json_object(resolved_workspace / _WORKTREE_STATE_FILE)
        history_payload = _load_json_object(resolved_workspace / _WORKTREE_HISTORY_FILE)

        if state_payload:
            persisted_repo_root = _coerce_path(
                state_payload.get('repo_root', state_payload.get('repoRoot', repo_root)),
                label='repo_root',
            )
            if persisted_repo_root != repo_root:
                raise ValueError('Persisted worktree state repo_root does not match current repository')

            persisted_common_dir = _coerce_path(
                state_payload.get('git_common_dir', state_payload.get('gitCommonDir', git_common_dir)),
                label='git_common_dir',
            )
            if persisted_common_dir != git_common_dir:
                raise ValueError('Persisted worktree state git_common_dir does not match current repository')

        managed_worktrees = tuple(
            ManagedWorktreeRecord.from_dict(item)
            for item in state_payload.get('managed_worktrees', state_payload.get('managedWorktrees', []))
            if isinstance(item, dict)
        )
        active_count = sum(1 for item in managed_worktrees if item.status is WorktreeStatus.ACTIVE)
        if active_count > 1:
            raise ValueError('Worktree state may contain at most one active managed worktree')

        history_records = tuple(
            WorktreeHistoryRecord.from_dict(item)
            for item in history_payload.get('events', [])
            if isinstance(item, dict)
        )

        current_cwd = _coerce_path(state_payload.get('current_cwd', state_payload.get('currentCwd', resolved_workspace)), label='current_cwd')
        return cls(
            workspace=resolved_workspace,
            repo_root=repo_root,
            git_common_dir=git_common_dir,
            current_cwd=current_cwd,
            managed_worktrees=managed_worktrees,
            history_records=history_records,
            schema_version=_as_int(state_payload.get('schema_version'), _SCHEMA_VERSION),
        )

    def save(self) -> tuple[Path, Path]:
        """把当前 runtime 状态与历史写回磁盘。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            tuple[Path, Path]: 状态文件路径与历史文件路径。
        Raises:
            OSError: 当底层文件写入失败时抛出。
        """
        state_path = self.workspace / _WORKTREE_STATE_FILE
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    'schema_version': self.schema_version,
                    'repo_root': str(self.repo_root),
                    'git_common_dir': str(self.git_common_dir),
                    'current_cwd': str(self.current_cwd),
                    'managed_worktrees': [item.to_dict() for item in self.managed_worktrees],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

        history_path = self.workspace / _WORKTREE_HISTORY_FILE
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            json.dumps(
                {
                    'schema_version': self.schema_version,
                    'events': [item.to_dict() for item in self.history_records],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        return state_path, history_path

    def list_worktrees(self, *, status: WorktreeStatus | None = None) -> tuple[ManagedWorktreeRecord, ...]:
        """列出当前 runtime 可见的受管工作树记录。

        Args:
            status (WorktreeStatus | None): 可选状态过滤；为空时返回全部记录。
        Returns:
            tuple[ManagedWorktreeRecord, ...]: 匹配条件的受管工作树记录列表。
        """
        if status is None:
            return self.managed_worktrees
        return tuple(item for item in self.managed_worktrees if item.status is status)

    def active_worktree(self) -> ManagedWorktreeRecord | None:
        """返回当前激活中的受管工作树。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            ManagedWorktreeRecord | None: 当前激活记录；若没有则返回 None。
        """
        for item in self.managed_worktrees:
            if item.status is WorktreeStatus.ACTIVE:
                return item
        return None

    def enter_worktree(
        self,
        branch: str,
        *,
        path: Path | None = None,
        base_ref: str = 'HEAD',
    ) -> ManagedWorktreeRecord:
        """创建并进入一个新的受管工作树。

        Args:
            branch (str): 需要创建的新分支名。
            path (Path | None): 可选工作树路径；为空时按默认 sibling 策略生成。
            base_ref (str): 创建工作树时使用的起始 ref。
        Returns:
            ManagedWorktreeRecord: 新创建并激活的工作树记录。
        Raises:
            ValueError: 当已存在激活工作树、分支名非法、目标路径已存在或 git 命令失败时抛出。
        """
        if self.active_worktree() is not None:
            raise ValueError('A managed worktree is already active; exit it before entering another one')

        normalized_branch = _normalize_branch_name(self.repo_root, branch)
        if any(item.branch == normalized_branch and item.status is not WorktreeStatus.REMOVED for item in self.managed_worktrees):
            raise ValueError(f'Managed worktree branch already exists in runtime state: {normalized_branch!r}')

        worktree_path = self._resolve_target_path(path, normalized_branch)
        if worktree_path.exists():
            raise ValueError(f'Worktree path already exists: {worktree_path}')

        _run_git(self.repo_root, 'worktree', 'add', '-b', normalized_branch, str(worktree_path), base_ref)
        timestamp = _utc_now()
        record = ManagedWorktreeRecord(
            worktree_id=uuid4().hex,
            branch=normalized_branch,
            path=worktree_path,
            repo_root=self.repo_root,
            git_common_dir=self.git_common_dir,
            previous_cwd=self.current_cwd,
            status=WorktreeStatus.ACTIVE,
            base_ref=base_ref.strip() or 'HEAD',
            created_at=timestamp,
            updated_at=timestamp,
        )
        history_record = WorktreeHistoryRecord(
            event_id=uuid4().hex,
            action=WorktreeHistoryAction.ENTER,
            worktree_id=record.worktree_id,
            branch=record.branch,
            path=record.path,
            cwd_before=record.previous_cwd,
            cwd_after=record.path,
            recorded_at=timestamp,
            message='Created managed worktree and switched logical cwd.',
        )
        self._commit(
            managed_worktrees=self.managed_worktrees + (record,),
            current_cwd=record.path,
            history_record=history_record,
        )
        return record

    def exit_worktree(self, *, remove: bool) -> ManagedWorktreeRecord:
        """退出当前激活工作树，并决定保留或删除底层目录。

        Args:
            remove (bool): 为 True 时执行 `git worktree remove`；为 False 时只退出并保留目录。
        Returns:
            ManagedWorktreeRecord: 更新后的工作树记录。
        Raises:
            ValueError: 当没有激活工作树、remove 时工作树脏、或 git 命令失败时抛出。
        """
        active_record = self.active_worktree()
        if active_record is None:
            raise ValueError('No active managed worktree to exit')

        if remove and _is_worktree_dirty(active_record.path):
            raise ValueError(f'Cannot remove dirty worktree: {active_record.path}')

        if remove:
            _run_git(self.repo_root, 'worktree', 'remove', str(active_record.path))

        timestamp = _utc_now()
        updated_record = replace(
            active_record,
            status=WorktreeStatus.REMOVED if remove else WorktreeStatus.EXITED,
            updated_at=timestamp,
        )
        history_record = WorktreeHistoryRecord(
            event_id=uuid4().hex,
            action=WorktreeHistoryAction.EXIT_REMOVE if remove else WorktreeHistoryAction.EXIT_KEEP,
            worktree_id=updated_record.worktree_id,
            branch=updated_record.branch,
            path=updated_record.path,
            cwd_before=active_record.path,
            cwd_after=active_record.previous_cwd,
            recorded_at=timestamp,
            message=(
                'Removed managed worktree and restored logical cwd.'
                if remove
                else 'Exited managed worktree, kept directory, and restored logical cwd.'
            ),
        )
        next_records = tuple(
            updated_record if item.worktree_id == updated_record.worktree_id else item
            for item in self.managed_worktrees
        )
        self._commit(
            managed_worktrees=next_records,
            current_cwd=updated_record.previous_cwd,
            history_record=history_record,
        )
        return updated_record

    def _resolve_target_path(self, path: Path | None, branch: str) -> Path:
        """解析 enter_worktree 的目标路径。

        Args:
            path (Path | None): 调用方显式传入的路径。
            branch (str): 已归一化的分支名。
        Returns:
            Path: 解析后的绝对工作树路径。
        """
        if path is None:
            return (self.workspace.parent / f'{self.workspace.name}--wt--{_slugify_branch(branch)}').resolve()
        if path.is_absolute():
            return path.resolve()
        return (self.workspace.parent / path).resolve()

    def _commit(
        self,
        *,
        managed_worktrees: tuple[ManagedWorktreeRecord, ...],
        current_cwd: Path,
        history_record: WorktreeHistoryRecord | None,
    ) -> None:
        """提交一次内存态更新并立即持久化。

        Args:
            managed_worktrees (tuple[ManagedWorktreeRecord, ...]): 更新后的全部工作树记录。
            current_cwd (Path): 更新后的逻辑 cwd。
            history_record (WorktreeHistoryRecord | None): 本次需要追加的历史事件。
        Returns:
            None: 该方法原地更新内存态并触发持久化。
        Raises:
            OSError: 当状态文件写入失败时抛出。
        """
        self.managed_worktrees = managed_worktrees
        self.current_cwd = current_cwd.resolve()
        if history_record is not None:
            self.history_records = self.history_records + (history_record,)
        self.save()


def _detect_repo_root(workspace: Path) -> Path:
    """探测给定工作区对应的 git 仓库顶层目录。

    Args:
        workspace (Path): 当前工作区目录。
    Returns:
        Path: git 仓库顶层目录的绝对路径。
    Raises:
        ValueError: 当工作区不在 git 仓库内或 git 命令失败时抛出。
    """
    output = _run_git_stdout(workspace, 'rev-parse', '--show-toplevel')
    return Path(output).resolve()


def _detect_git_common_dir(repo_root: Path) -> Path:
    """探测仓库共享的 git common dir。

    Args:
        repo_root (Path): 仓库顶层目录。
    Returns:
        Path: 共享 git common dir 的绝对路径。
    Raises:
        ValueError: 当 git 命令失败时抛出。
    """
    output = _run_git_stdout(repo_root, 'rev-parse', '--git-common-dir')
    candidate = Path(output)
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / candidate).resolve()


def _run_git_stdout(cwd: Path, *args: str) -> str:
    """执行 git 命令并返回裁剪后的标准输出。

    Args:
        cwd (Path): git 命令执行目录。
        *args (str): git 子命令与参数列表。
    Returns:
        str: 标准输出文本，已去除首尾空白。
    Raises:
        ValueError: 当 git 命令失败时抛出。
    """
    completed = _run_git(cwd, *args)
    return completed.stdout.strip()


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """执行单条 git 命令并统一错误信息。

    Args:
        cwd (Path): git 命令执行目录。
        *args (str): git 子命令与参数列表。
    Returns:
        subprocess.CompletedProcess[str]: 已完成的进程结果。
    Raises:
        ValueError: 当 git 返回非零退出码时抛出。
    """
    completed = subprocess.run(
        ['git', *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or 'unknown git error'
        command = ' '.join(['git', *args])
        raise ValueError(f'Git command failed: {command}: {stderr}')
    return completed


def _is_worktree_dirty(worktree_path: Path) -> bool:
    """检查目标工作树是否存在未提交变更。

    Args:
        worktree_path (Path): 需要检查的工作树目录。
    Returns:
        bool: 若存在未提交或未跟踪文件则返回 True。
    Raises:
        ValueError: 当 git 状态命令失败时抛出。
    """
    status_output = _run_git_stdout(worktree_path, 'status', '--porcelain')
    return bool(status_output.strip())


def _normalize_branch_name(repo_root: Path, branch: str) -> str:
    """校验并归一化待创建的分支名。

    Args:
        repo_root (Path): 当前仓库根目录。
        branch (str): 原始分支名。
    Returns:
        str: git 校验后的合法分支名。
    Raises:
        ValueError: 当分支名为空或不符合 git 规则时抛出。
    """
    candidate = str(branch).strip()
    if not candidate:
        raise ValueError('branch must not be empty')
    return _run_git_stdout(repo_root, 'check-ref-format', '--branch', candidate)


def _load_json_object(path: Path) -> JSONDict:
    """按 JSON object 语义读取文件。

    Args:
        path (Path): 需要读取的文件路径。
    Returns:
        JSONDict: 解析后的 JSON 对象；文件不存在时返回空对象。
    Raises:
        ValueError: 当 JSON 顶层不是对象时抛出。
        json.JSONDecodeError: 当 JSON 语法非法时抛出。
    """
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'JSON file must contain an object: {path}')
    return dict(payload)


def _coerce_path(value: object, *, label: str) -> Path:
    """把任意持久化路径字段转换为绝对 Path。

    Args:
        value (object): 原始路径字段。
        label (str): 字段名，用于错误消息。
    Returns:
        Path: 解析后的绝对路径。
    Raises:
        ValueError: 当路径字段为空时抛出。
    """
    text = str(value or '').strip()
    if not text:
        raise ValueError(f'{label} must not be empty')
    return Path(text).resolve()


def _normalize_identifier(value: object, *, label: str) -> str:
    """规范化通用标识符字段。

    Args:
        value (object): 原始标识符值。
        label (str): 字段名，用于错误提示。
    Returns:
        str: 去除首尾空白后的标识符。
    Raises:
        ValueError: 当标识符为空时抛出。
    """
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    return normalized


def _as_int(value: object, default: int) -> int:
    """把持久化整数字段转为 int，并在失败时回退默认值。

    Args:
        value (object): 原始字段值。
        default (int): 转换失败时使用的默认值。
    Returns:
        int: 转换后的整数值。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    """生成当前 UTC 时间戳。

    Args:
        None: 该函数不接收额外参数。
    Returns:
        str: ISO-8601 UTC 时间字符串。
    """
    return datetime.now(timezone.utc).isoformat()


def _slugify_branch(branch: str) -> str:
    """把分支名转换为适合默认工作树目录的安全片段。

    Args:
        branch (str): 原始分支名。
    Returns:
        str: 仅包含安全字符的目录片段。
    """
    compact = _PATH_TOKEN_PATTERN.sub('-', branch.strip())
    compact = compact.strip('-._')
    return compact or 'worktree'