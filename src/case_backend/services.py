"""案件服务：受理、分派、认领、行动、转介、暂停、升级、关闭、重开与合并。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .errors import (
    ClaimConflictError,
    InvalidTransitionError,
    MergeError,
    NotFoundError,
    PendingDependencyError,
    PermissionDeniedError,
    ValidationError,
)
from .models import (
    ActionKind,
    ActionRecord,
    Actor,
    Case,
    CaseState,
    ExternalDependency,
    MergedSource,
    Role,
    Sensitivity,
    StateChange,
)
from .repository import InMemoryCaseRepository
from .routing import RoutingTable
from .states import apply_transition


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_ids(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class CaseService:
    """国际学生支持案件的应用服务。

    - 分派依据：问题类型 + 机构权限 + 学生同意范围（见 routing.RoutingTable）
    - 认领在仓储锁内原子完成，只成功一次
    - 所有状态推进经 states.apply_transition 校验并留痕
    - 每个 mutator 都先做全部校验、再落字段，避免锁内部分写入
    """

    def __init__(
        self,
        repo: InMemoryCaseRepository,
        routing: RoutingTable,
        *,
        clock: Callable[[], datetime] = _utcnow,
        id_factory: Callable[[str], str] = _default_ids,
    ) -> None:
        self._repo = repo
        self._routing = routing
        self._clock = clock
        self._ids = id_factory

    @property
    def repo(self) -> InMemoryCaseRepository:
        return self._repo

    @property
    def routing(self) -> RoutingTable:
        return self._routing

    def now(self) -> datetime:
        return self._clock()

    # ---- 受理与同意范围 ----

    def intake(
        self,
        *,
        student_id: str,
        issue_type: str,
        source_office: str,
        description: str,
        consent_groups: frozenset[str] | set[str] | list[str],
        actor: Actor,
    ) -> Case:
        """学生向某办公室求助，登记为一条新案件（线索）。"""
        if actor.role is Role.STUDENT and actor.actor_id != student_id:
            raise PermissionDeniedError("学生只能为自己发起求助")
        now = self._clock()
        case = Case(
            case_id=self._ids("case"),
            student_id=student_id,
            issue_type=issue_type,
            source_office=source_office,
            description=description,
            created_at=now,
            consent_groups=frozenset(consent_groups),
        )
        case.history.append(StateChange(None, CaseState.INTAKE, actor, now, f"经{source_office}求助"))
        return self._repo.add(case)

    def grant_consent(self, case_id: str, group_id: str, *, actor: Actor) -> Case:
        """学生扩大同意范围，使自动分派可以落到该处理组。"""
        if group_id not in self._routing.groups:
            raise NotFoundError(f"处理组不存在：{group_id}")

        def _do(case: Case) -> None:
            if actor.role is Role.STUDENT and actor.actor_id != case.student_id:
                raise PermissionDeniedError("只有学生本人可以扩大同意范围")
            if actor.role not in (Role.STUDENT, Role.COORDINATOR):
                raise PermissionDeniedError("同意范围只能由学生本人或协调员登记")
            case.consent_groups = case.consent_groups | {group_id}

        return self._repo.update(case_id, _do)

    # ---- 分派与认领 ----

    def triage(self, case_id: str, *, actor: Actor) -> Case:
        """按问题类型 + 机构权限 + 同意范围自动分派，并设定服务时限。"""
        self._require_staff(actor)

        def _do(case: Case) -> None:
            group = self._routing.route(case.issue_type, case.consent_groups)
            apply_transition(case, "triage", actor, self._clock(), f"分派至{group.name}")
            case.group_id = group.group_id
            case.response_due_at = case.created_at + timedelta(hours=group.sla.response_hours)
            case.resolution_due_at = case.created_at + timedelta(hours=group.sla.resolution_hours)

        return self._repo.update(case_id, _do)

    def claim(self, case_id: str, *, specialist: Actor) -> Case:
        """认领案件；在仓储锁内原子判定，并发下只有第一个成功。"""
        if specialist.role is not Role.SPECIALIST:
            raise PermissionDeniedError("只有支持专员可以认领案件")

        def _do(case: Case) -> None:
            if case.assignee_id is not None:
                raise ClaimConflictError(f"案件 {case.case_id} 已被认领")
            if case.state is not CaseState.ASSIGNED:
                raise InvalidTransitionError(
                    f"案件 {case.case_id} 当前状态「{case.state.value}」不能认领"
                )
            if specialist.group_id != case.group_id:
                raise PermissionDeniedError("只能认领本分派组的案件")
            apply_transition(case, "claim", specialist, self._clock(), "认领")
            case.assignee_id = specialist.actor_id

        return self._repo.update(case_id, _do)

    # ---- 处置行动 ----

    def record_action(
        self,
        case_id: str,
        *,
        actor: Actor,
        kind: ActionKind | str,
        detail: str,
        student_visible: bool = False,
        material_key: str | None = None,
        sensitivity: Sensitivity = Sensitivity.MEDIUM,
    ) -> Case:
        """记录一次处置行动；student_visible 决定学生是否可见。"""
        self._require_staff(actor)
        action_kind = ActionKind(kind)

        def _do(case: Case) -> None:
            if case.state is CaseState.CLOSED:
                raise InvalidTransitionError("已关闭案件不能追加行动")
            self._require_case_group(actor, case)
            case.actions.append(
                ActionRecord(
                    action_id=self._ids("act"),
                    case_id=case.case_id,
                    actor=actor,
                    kind=action_kind,
                    detail=detail,
                    created_at=self._clock(),
                    student_visible=student_visible,
                    material_key=material_key,
                    sensitivity=sensitivity,
                )
            )

        return self._repo.update(case_id, _do)

    # ---- 转介与外部依赖 ----

    def refer(
        self,
        case_id: str,
        *,
        actor: Actor,
        target: str,
        description: str,
        expected_at: datetime | None = None,
    ) -> Case:
        """转介到外部机构/其他办公室，并登记外部依赖。"""
        self._require_staff(actor)

        def _do(case: Case) -> None:
            self._require_case_group(actor, case)
            apply_transition(case, "refer", actor, self._clock(), f"转介至{target}")
            case.dependencies.append(
                ExternalDependency(
                    dependency_id=self._ids("dep"),
                    case_id=case.case_id,
                    target=target,
                    description=description,
                    created_at=self._clock(),
                    expected_at=expected_at,
                )
            )

        return self._repo.update(case_id, _do)

    def resolve_dependency(self, case_id: str, dependency_id: str, *, actor: Actor) -> Case:
        """了结外部依赖；若案件处于转介且依赖全部了结，自动回到处置。"""
        self._require_staff(actor)

        def _do(case: Case) -> None:
            dep = next((d for d in case.dependencies if d.dependency_id == dependency_id), None)
            if dep is None:
                raise NotFoundError(f"外部依赖不存在：{dependency_id}")
            if dep.resolved_at is not None:
                raise InvalidTransitionError("该外部依赖已了结")
            dep.resolved_at = self._clock()
            if case.state is CaseState.REFERRED and not any(d.pending for d in case.dependencies):
                apply_transition(case, "resolve", actor, self._clock(), "外部依赖了结")

        return self._repo.update(case_id, _do)

    # ---- 暂停、升级 ----

    def pause(self, case_id: str, *, actor: Actor, reason: str) -> Case:
        """暂停处置（如等待学生补充材料）；时限继续计时。"""
        self._require_staff(actor)
        if not reason:
            raise ValidationError("暂停必须填写原因")

        def _do(case: Case) -> None:
            self._require_case_group(actor, case)
            apply_transition(case, "pause", actor, self._clock(), reason)

        return self._repo.update(case_id, _do)

    def resume(self, case_id: str, *, actor: Actor) -> Case:
        self._require_staff(actor)

        def _do(case: Case) -> None:
            self._require_case_group(actor, case)
            apply_transition(case, "resume", actor, self._clock(), "恢复处置")

        return self._repo.update(case_id, _do)

    def escalate(self, case_id: str, *, actor: Actor, reason: str) -> Case:
        """升级到项目协调员。"""
        self._require_staff(actor)
        if not reason:
            raise ValidationError("升级必须填写原因")

        def _do(case: Case) -> None:
            self._require_case_group(actor, case)
            apply_transition(case, "escalate", actor, self._clock(), reason)

        return self._repo.update(case_id, _do)

    def deescalate(self, case_id: str, *, actor: Actor) -> Case:
        """协调员把升级案件退回处理组继续处置。"""
        if actor.role is not Role.COORDINATOR:
            raise PermissionDeniedError("只有项目协调员可以退回升级案件")

        def _do(case: Case) -> None:
            apply_transition(case, "deescalate", actor, self._clock(), "退回处理组")

        return self._repo.update(case_id, _do)

    # ---- 关闭与重开 ----

    def close(self, case_id: str, *, actor: Actor, outcome: str, student_summary: str = "") -> Case:
        """关闭案件并记录结果；存在未决外部依赖时不能关闭。"""
        self._require_staff(actor)
        if not outcome:
            raise ValidationError("关闭必须填写处理结果")

        def _do(case: Case) -> None:
            self._require_case_group(actor, case)
            pending = [d for d in case.dependencies if d.pending]
            if pending:
                raise PendingDependencyError(f"存在 {len(pending)} 项未决外部依赖，不能关闭")
            apply_transition(case, "close", actor, self._clock(), outcome)
            case.closed_at = self._clock()
            case.close_outcome = outcome
            case.close_reason = "closed"
            case.student_summary = student_summary

        return self._repo.update(case_id, _do)

    def reopen(self, case_id: str, *, actor: Actor, reason: str) -> Case:
        """重开已关闭案件；被合并的重复线索保留来源，不能重开。"""
        self._require_staff(actor)
        if not reason:
            raise ValidationError("重开必须填写原因")

        def _do(case: Case) -> None:
            if case.close_reason == "merged":
                raise InvalidTransitionError("已合并的重复线索保留来源，不能重开")
            apply_transition(case, "reopen", actor, self._clock(), reason)
            case.closed_at = None
            case.close_outcome = None
            case.close_reason = None
            case.student_summary = ""
            case.reopen_count += 1
            if case.group_id is not None:
                sla = self._routing.groups[case.group_id].sla
                case.resolution_due_at = self._clock() + timedelta(hours=sla.resolution_hours)

        return self._repo.update(case_id, _do)

    # ---- 重复线索合并 ----

    def find_duplicate_leads(self, student_id: str) -> list[Case]:
        """同一学生名下多于一条未关闭案件时，视为重复线索候选。"""
        opens = [c for c in self._repo.find_by_student(student_id) if c.open]
        return sorted(opens, key=lambda c: c.created_at) if len(opens) > 1 else []

    def merge(self, primary_id: str, duplicate_id: str, *, actor: Actor) -> Case:
        """把重复线索并入主案件。

        被合并线索以「merged」原因关闭并保留完整来源（编号、来源办公室、
        行动流水），主案件记录 merged_sources；合并后的线索不能重开。
        """
        self._require_staff(actor)
        if primary_id == duplicate_id:
            raise MergeError("案件不能与自身合并")
        with self._repo.transaction():
            primary = self._repo.get(primary_id)
            duplicate = self._repo.get(duplicate_id)
            if primary.student_id != duplicate.student_id:
                raise MergeError("不同学生的案件不能合并")
            if primary.merged_into is not None or primary.close_reason == "merged":
                raise MergeError("主案件本身是被合并的线索，请并入最终主案件")
            if duplicate.merged_into is not None or duplicate.close_reason == "merged":
                raise MergeError("该线索已被合并过")
            if not primary.open:
                raise MergeError("主案件已关闭，不能并入")
            if not duplicate.open:
                raise MergeError("重复线索已关闭，不能合并")
            now = self._clock()
            apply_transition(duplicate, "close", actor, now, f"合并入 {primary.case_id}")
            duplicate.merged_into = primary.case_id
            duplicate.close_reason = "merged"
            duplicate.close_outcome = f"重复线索，合并入 {primary.case_id}"
            duplicate.closed_at = now
            duplicate.version += 1
            primary.merged_sources.append(
                MergedSource(
                    case_id=duplicate.case_id,
                    source_office=duplicate.source_office,
                    intake_at=duplicate.created_at,
                    merged_at=now,
                )
            )
            primary.version += 1
            return primary

    # ---- 内部校验 ----

    @staticmethod
    def _require_staff(actor: Actor) -> None:
        if actor.role not in (Role.SPECIALIST, Role.COORDINATOR):
            raise PermissionDeniedError("该操作需要支持专员或项目协调员身份")

    @staticmethod
    def _require_case_group(actor: Actor, case: Case) -> None:
        if actor.role is Role.SPECIALIST and actor.group_id != case.group_id:
            raise PermissionDeniedError("只能操作本分派组的案件")
