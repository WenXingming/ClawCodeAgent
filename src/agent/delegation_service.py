"""负责子代理记录、分组与依赖批处理。

本模块提供主循环中的 delegate_agent 编排底座，聚焦三类职责：为父代理维护 child agent、group 与 lineage 记录；根据委派任务依赖关系生成稳定批次；汇总子代理 stop_reason、失败数与 dependency skip 统计，供上层事件与结果摘要复用。

该模块不直接执行模型调用；真正的 child run/resume 仍由 agent facade 触发。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from core_contracts.protocol import JSONDict


class ManagedAgentStatus(StrEnum):
    """受管代理记录的稳定状态集合。"""

    RUNNING = 'running'
    COMPLETED = 'completed'
    SKIPPED = 'skipped'


@dataclass(frozen=True)
class DelegatedTaskSpec:
    """描述一条 delegate_agent 子任务规格。

    该对象由 tool 参数解析而来，随后会被 DelegationService 用于生成依赖批次，
    并在执行完成后与 child agent record 关联。
    """

    task_id: str  # str: 子任务稳定标识，用于依赖引用和 lineage 追踪。
    prompt: str  # str: 交给 child agent 执行的任务提示词。
    label: str | None = None  # str | None: 面向人类展示的可选标签。
    dependencies: tuple[str, ...] = ()  # tuple[str, ...]: 当前子任务依赖的上游 task_id 列表。
    resume_session_id: str | None = None  # str | None: 若不为空，则 child agent 从该 session 续跑。

    def to_dict(self) -> JSONDict:
        """把子任务规格转换为可持久化字典。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            JSONDict: 适合写入 JSON 的结构化对象。
        """
        payload: JSONDict = {
            'task_id': self.task_id,
            'prompt': self.prompt,
            'dependencies': list(self.dependencies),
        }
        if self.label is not None:
            payload['label'] = self.label
        if self.resume_session_id is not None:
            payload['resume_session_id'] = self.resume_session_id
        return payload

    @classmethod
    def from_dict(cls, payload: JSONDict | None) -> 'DelegatedTaskSpec':
        """从 JSON 字典恢复子任务规格。

        Args:
            payload (JSONDict | None): 原始工具参数对象。
        Returns:
            DelegatedTaskSpec: 解析并校验后的子任务规格。
        Raises:
            ValueError: 当 task_id、prompt 或 dependencies 非法时抛出。
        """
        data = dict(payload or {})
        task_id = _normalize_identifier(data.get('task_id', data.get('taskId', '')), label='task_id')
        prompt = str(data.get('prompt', '')).strip()
        if not prompt:
            raise ValueError(f'Delegated task {task_id!r} requires non-empty prompt')
        return cls(
            task_id=task_id,
            prompt=prompt,
            label=_normalize_optional_text(data.get('label')),
            dependencies=_normalize_dependencies(data.get('dependencies', []), task_id=task_id),
            resume_session_id=_normalize_optional_text(
                data.get('resume_session_id', data.get('resumeSessionId'))
            ),
        )


@dataclass(frozen=True)
class ManagedAgentRecord:
    """描述一个受管 agent 节点的稳定状态。"""

    agent_id: str  # str: 受管代理唯一标识。
    prompt: str  # str: 该代理收到的实际 prompt。
    parent_agent_id: str | None = None  # str | None: 父代理标识；根代理为空。
    group_id: str | None = None  # str | None: 所属 group 标识。
    child_index: int | None = None  # int | None: 当前 child 在 group 中的稳定序号。
    label: str | None = None  # str | None: 面向用户显示的标签。
    task_id: str | None = None  # str | None: 若来自 delegate batch，则关联的 task_id。
    resumed_from_session_id: str | None = None  # str | None: 续跑来源 session_id。
    session_id: str | None = None  # str | None: child agent 最终产出的 session_id。
    session_path: str | None = None  # str | None: child agent 最终产出的 session 文件路径。
    status: ManagedAgentStatus = ManagedAgentStatus.RUNNING  # ManagedAgentStatus: 当前受管代理状态。
    turns: int = 0  # int: child agent 运行总轮数。
    tool_calls: int = 0  # int: child agent 发生的工具调用次数。
    stop_reason: str | None = None  # str | None: child agent 最终 stop_reason。


@dataclass(frozen=True)
class ManagedAgentGroup:
    """描述一组 delegate_agent 子任务的聚合状态。"""

    group_id: str  # str: 组唯一标识。
    label: str | None = None  # str | None: 组标签。
    parent_agent_id: str | None = None  # str | None: 触发该 group 的父代理标识。
    child_agent_ids: tuple[str, ...] = ()  # tuple[str, ...]: 当前组下全部 child agent 标识。
    strategy: str = 'serial'  # str: 当前组执行策略，首版固定为 serial。
    status: str = 'running'  # str: 组状态，首版使用 completed / completed_with_failures / running。
    completed_children: int = 0  # int: 成功或已完成 child 数量。
    failed_children: int = 0  # int: 失败 child 数量。
    batch_count: int = 0  # int: 依赖批次数量。
    max_batch_size: int = 0  # int: 单个 batch 的最大 child 数量。
    dependency_skips: int = 0  # int: 因依赖失败而跳过的 child 数量。


@dataclass
class DelegationService:
    """维护 delegate_agent 运行期间的 child 代理与 group 元数据。

    典型工作流如下：
    1. 父代理通过 `start_agent()` 注册自身或 child 记录。
    2. delegate_agent 创建 group，并用 `plan_batches()` 生成依赖批次。
    3. 每个 child 执行结束后调用 `finish_agent()` 或 `skip_agent()`。
    4. 全部完成后调用 `finish_group()` 并通过 `group_summary()` 取汇总结果。
    """

    records: dict[str, ManagedAgentRecord] = field(default_factory=dict)  # dict[str, ManagedAgentRecord]: 全部受管代理记录。
    groups: dict[str, ManagedAgentGroup] = field(default_factory=dict)  # dict[str, ManagedAgentGroup]: 全部 child group 记录。
    _counter: int = 0  # int: agent_id 自增计数器。
    _group_counter: int = 0  # int: group_id 自增计数器。

    def start_agent(
        self,
        *,
        prompt: str,
        parent_agent_id: str | None = None,
        group_id: str | None = None,
        child_index: int | None = None,
        label: str | None = None,
        task_id: str | None = None,
        resumed_from_session_id: str | None = None,
    ) -> str:
        """注册一个新的受管代理记录。

        Args:
            prompt (str): 该代理将要执行的 prompt。
            parent_agent_id (str | None): 父代理标识。
            group_id (str | None): 所属 group 标识。
            child_index (int | None): 子代理顺序编号。
            label (str | None): 可选显示标签。
            task_id (str | None): 关联的委派任务标识。
            resumed_from_session_id (str | None): 续跑来源 session_id。
        Returns:
            str: 新创建的 agent_id。
        """
        self._counter += 1
        agent_id = f'agent_{self._counter}'
        self.records[agent_id] = ManagedAgentRecord(
            agent_id=agent_id,
            prompt=prompt,
            parent_agent_id=parent_agent_id,
            group_id=group_id,
            child_index=child_index,
            label=label,
            task_id=task_id,
            resumed_from_session_id=resumed_from_session_id,
        )
        if group_id is not None:
            self.register_group_child(group_id, agent_id, child_index=child_index)
        return agent_id

    def finish_agent(
        self,
        agent_id: str,
        *,
        session_id: str | None,
        session_path: str | None,
        turns: int,
        tool_calls: int,
        stop_reason: str | None,
    ) -> None:
        """把一个受管代理标记为完成。

        Args:
            agent_id (str): 需要完成的代理标识。
            session_id (str | None): 该代理最终 session_id。
            session_path (str | None): 该代理最终 session 文件路径。
            turns (int): 该代理总轮数。
            tool_calls (int): 该代理总工具调用次数。
            stop_reason (str | None): 该代理最终 stop_reason。
        Returns:
            None: 该方法直接更新内存状态。
        """
        current = self.records.get(agent_id)
        if current is None:
            return
        self.records[agent_id] = replace(
            current,
            session_id=session_id,
            session_path=session_path,
            status=ManagedAgentStatus.COMPLETED,
            turns=turns,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )

    def skip_agent(self, agent_id: str, *, reason: str) -> None:
        """把一个受管代理标记为跳过。

        Args:
            agent_id (str): 需要跳过的代理标识。
            reason (str): 跳过原因，通常为 dependency_skipped。
        Returns:
            None: 该方法直接更新内存状态。
        """
        current = self.records.get(agent_id)
        if current is None:
            return
        self.records[agent_id] = replace(
            current,
            status=ManagedAgentStatus.SKIPPED,
            stop_reason=reason,
        )

    def start_group(
        self,
        *,
        label: str | None = None,
        parent_agent_id: str | None = None,
        strategy: str = 'serial',
    ) -> str:
        """创建一个新的 delegate child group。

        Args:
            label (str | None): 组标签。
            parent_agent_id (str | None): 父代理标识。
            strategy (str): 组执行策略，首版固定为 serial。
        Returns:
            str: 新创建的 group_id。
        """
        self._group_counter += 1
        group_id = f'group_{self._group_counter}'
        self.groups[group_id] = ManagedAgentGroup(
            group_id=group_id,
            label=label,
            parent_agent_id=parent_agent_id,
            strategy=strategy,
        )
        return group_id

    def register_group_child(
        self,
        group_id: str,
        agent_id: str,
        *,
        child_index: int | None = None,
    ) -> None:
        """把受管代理挂到指定 group 下。

        Args:
            group_id (str): 目标 group 标识。
            agent_id (str): child agent 标识。
            child_index (int | None): 子代理顺序编号。
        Returns:
            None: 该方法直接更新 group 与 record。
        """
        group = self.groups.get(group_id)
        record = self.records.get(agent_id)
        if group is None or record is None:
            return

        child_agent_ids = group.child_agent_ids
        if agent_id not in child_agent_ids:
            child_agent_ids = child_agent_ids + (agent_id,)
        self.groups[group_id] = replace(group, child_agent_ids=child_agent_ids)
        self.records[agent_id] = replace(record, group_id=group_id, child_index=child_index)

    def finish_group(
        self,
        group_id: str,
        *,
        status: str,
        completed_children: int,
        failed_children: int,
        batch_count: int,
        max_batch_size: int,
        dependency_skips: int,
    ) -> None:
        """写回 child group 的最终聚合状态。

        Args:
            group_id (str): 目标 group 标识。
            status (str): group 最终状态。
            completed_children (int): 已完成 child 数量。
            failed_children (int): 失败 child 数量。
            batch_count (int): 依赖批次数量。
            max_batch_size (int): 最大批次大小。
            dependency_skips (int): 因依赖失败而跳过的 child 数量。
        Returns:
            None: 该方法直接更新 group 状态。
        """
        group = self.groups.get(group_id)
        if group is None:
            return
        self.groups[group_id] = replace(
            group,
            status=status,
            completed_children=completed_children,
            failed_children=failed_children,
            batch_count=batch_count,
            max_batch_size=max_batch_size,
            dependency_skips=dependency_skips,
        )

    def plan_batches(
        self,
        tasks: tuple[DelegatedTaskSpec, ...] | list[DelegatedTaskSpec],
    ) -> tuple[tuple[DelegatedTaskSpec, ...], ...]:
        """按依赖关系为 delegate 子任务生成稳定批次。

        生成规则：
        1. 同一批次中的任务都只依赖此前批次中的任务。
        2. 同一批次内部保持原始输入顺序。
        3. 若出现未知依赖或循环依赖，立即抛出异常。

        Args:
            tasks (tuple[DelegatedTaskSpec, ...] | list[DelegatedTaskSpec]): 需要批处理的子任务列表。
        Returns:
            tuple[tuple[DelegatedTaskSpec, ...], ...]: 按批次分组后的任务列表。
        Raises:
            ValueError: 当 task_id 重复、依赖未知或存在循环依赖时抛出。
        """
        normalized_tasks = self._normalize_task_specs(tasks)
        remaining_by_id = {item.task_id: item for item in normalized_tasks}
        completed_ids: set[str] = set()
        batches: list[tuple[DelegatedTaskSpec, ...]] = []

        while remaining_by_id:
            ready_batch = tuple(
                item
                for item in normalized_tasks
                if item.task_id in remaining_by_id and set(item.dependencies).issubset(completed_ids)
            )
            if not ready_batch:
                unresolved = ', '.join(sorted(remaining_by_id))
                raise ValueError(f'Circular delegated task dependencies detected: {unresolved}')
            batches.append(ready_batch)
            for item in ready_batch:
                completed_ids.add(item.task_id)
                remaining_by_id.pop(item.task_id, None)
        return tuple(batches)

    def children_of(self, agent_id: str) -> tuple[ManagedAgentRecord, ...]:
        """返回指定父代理下的全部 child 记录。

        Args:
            agent_id (str): 父代理标识。
        Returns:
            tuple[ManagedAgentRecord, ...]: 按 child_index 排序后的 child 记录。
        """
        return tuple(
            sorted(
                (record for record in self.records.values() if record.parent_agent_id == agent_id),
                key=lambda item: (item.child_index is None, item.child_index or 0, item.agent_id),
            )
        )

    def group_children(self, group_id: str) -> tuple[ManagedAgentRecord, ...]:
        """返回指定 group 下的全部 child 记录。

        Args:
            group_id (str): group 标识。
        Returns:
            tuple[ManagedAgentRecord, ...]: 按 child_index 排序后的 child 记录。
        """
        return tuple(
            sorted(
                (record for record in self.records.values() if record.group_id == group_id),
                key=lambda item: (item.child_index is None, item.child_index or 0, item.agent_id),
            )
        )

    def child_agent_count(self) -> int:
        """统计当前 manager 中的 child agent 数量。

        Args:
            None: 该方法不接收额外参数。
        Returns:
            int: parent_agent_id 非空的受管代理数量。
        """
        return sum(1 for record in self.records.values() if record.parent_agent_id is not None)

    def group_summary(self, group_id: str) -> JSONDict | None:
        """生成指定 group 的聚合摘要。

        Args:
            group_id (str): group 标识。
        Returns:
            JSONDict | None: 聚合摘要；若 group 不存在则返回 None。
        """
        group = self.groups.get(group_id)
        if group is None:
            return None

        stop_reason_counts: dict[str, int] = {}
        resumed_children = 0
        skipped_children = 0
        children = self.group_children(group_id)
        for child in children:
            if child.resumed_from_session_id:
                resumed_children += 1
            if child.status is ManagedAgentStatus.SKIPPED:
                skipped_children += 1
            stop_reason = child.stop_reason or 'n/a'
            stop_reason_counts[stop_reason] = stop_reason_counts.get(stop_reason, 0) + 1

        return {
            'group_id': group.group_id,
            'label': group.label,
            'parent_agent_id': group.parent_agent_id,
            'strategy': group.strategy,
            'status': group.status,
            'child_count': len(children),
            'completed_children': group.completed_children,
            'failed_children': group.failed_children,
            'skipped_children': skipped_children,
            'resumed_children': resumed_children,
            'batch_count': group.batch_count,
            'max_batch_size': group.max_batch_size,
            'dependency_skips': group.dependency_skips,
            'stop_reason_counts': stop_reason_counts,
        }

    @staticmethod
    def _normalize_task_specs(
        tasks: tuple[DelegatedTaskSpec, ...] | list[DelegatedTaskSpec],
    ) -> tuple[DelegatedTaskSpec, ...]:
        """校验并标准化委派任务列表。

        Args:
            tasks (tuple[DelegatedTaskSpec, ...] | list[DelegatedTaskSpec]): 原始任务列表。
        Returns:
            tuple[DelegatedTaskSpec, ...]: 校验后的稳定任务元组。
        Raises:
            ValueError: 当元素类型错误、task_id 重复或依赖未知时抛出。
        """
        normalized_tasks: list[DelegatedTaskSpec] = []
        seen_ids: set[str] = set()
        for task in tasks:
            if not isinstance(task, DelegatedTaskSpec):
                raise ValueError('plan_batches expects DelegatedTaskSpec items')
            if task.task_id in seen_ids:
                raise ValueError(f'Duplicate delegated task id: {task.task_id!r}')
            seen_ids.add(task.task_id)
            normalized_tasks.append(task)

        known_ids = {item.task_id for item in normalized_tasks}
        for task in normalized_tasks:
            unknown_dependencies = [item for item in task.dependencies if item not in known_ids]
            if unknown_dependencies:
                dependency_list = ', '.join(unknown_dependencies)
                raise ValueError(
                    f'Delegated task {task.task_id!r} depends on unknown tasks: {dependency_list}'
                )
        return tuple(normalized_tasks)


def _normalize_identifier(value: object, *, label: str) -> str:
    """规范化通用标识符字段。

    Args:
        value (object): 原始标识符值。
        label (str): 字段名，用于错误消息。
    Returns:
        str: 去除首尾空白后的标识符。
    Raises:
        ValueError: 当标识符为空时抛出。
    """
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{label} must not be empty')
    return normalized


def _normalize_optional_text(value: object) -> str | None:
    """规范化可选文本字段。

    Args:
        value (object): 原始文本值。
    Returns:
        str | None: 去除首尾空白后的文本；为空时返回 None。
    """
    normalized = str(value or '').strip()
    return normalized or None


def _normalize_dependencies(value: object, *, task_id: str) -> tuple[str, ...]:
    """规范化子任务依赖列表。

    Args:
        value (object): 原始依赖字段。
        task_id (str): 当前任务标识，用于错误消息。
    Returns:
        tuple[str, ...]: 去重且保序后的依赖 task_id 元组。
    Raises:
        ValueError: 当依赖字段不是列表，或依赖自身时抛出。
    """
    if value in (None, ''):
        return ()
    if not isinstance(value, list):
        raise ValueError(f'Delegated task {task_id!r} dependencies must be a list')

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        dependency_id = _normalize_identifier(item, label='dependency')
        if dependency_id == task_id:
            raise ValueError(f'Delegated task {task_id!r} must not depend on itself')
        if dependency_id in seen:
            continue
        seen.add(dependency_id)
        normalized.append(dependency_id)
    return tuple(normalized)