"""案件后端核心服务：登记、去重合并、分派、认领与状态推进。"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from .errors import (
    CaseNotFoundError,
    ClaimConflictError,
    ConsentViolationError,
    DomainError,
    InvalidTransitionError,
    MergeError,
    RoutingError,
    StaffNotFoundError,
    StaffPermissionError,
)
from .models import (
    ActionRecord,
    ActionType,
    ActorRole,
    Case,
    CaseState,
    ConsentScope,
    Deadline,
    ExternalDependency,
    IssueType,
    Material,
    Source,
    StaffMember,
)
from .privacy import visible_materials
from .routing import GROUPS, office_permits, route
from .views import coordinator_row as _coordinator_row
from .views import student_view as _student_view

# 状态机：转介、暂停、升级、重开都按此推进
TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.INTAKE: frozenset({CaseState.ASSIGNED}),
    CaseState.ASSIGNED: frozenset(
        {CaseState.IN_PROGRESS, CaseState.PAUSED, CaseState.CLOSED}
    ),
    CaseState.IN_PROGRESS: frozenset(
        {CaseState.REFERRED, CaseState.PAUSED, CaseState.CLOSED}
    ),
    CaseState.REFERRED: frozenset(
        {CaseState.ASSIGNED, CaseState.IN_PROGRESS, CaseState.PAUSED, CaseState.CLOSED}
    ),
    CaseState.PAUSED: frozenset(
        {CaseState.ASSIGNED, CaseState.IN_PROGRESS, CaseState.REFERRED}
    ),
    CaseState.CLOSED: frozenset({CaseState.INTAKE}),
}

ESCALATABLE_STATES = frozenset(
    {
        CaseState.ASSIGNED,
        CaseState.IN_PROGRESS,
        CaseState.REFERRED,
        CaseState.PAUSED,
    }
)

# 服务时限（小时）：首次响应 / 办结
SLA_RESPONSE_HOURS = {
    IssueType.WELLBEING: 4,
    IssueType.VISA: 8,
    IssueType.ACADEMIC: 24,
    IssueType.INTERNSHIP: 24,
    IssueType.FINANCIAL: 24,
}
SLA_RESOLUTION_HOURS = {
    IssueType.WELLBEING: 48,
    IssueType.VISA: 72,
    IssueType.FINANCIAL: 96,
    IssueType.ACADEMIC: 120,
    IssueType.INTERNSHIP: 120,
}

RESPONSE_DEADLINE = "首次响应"
RESOLUTION_DEADLINE = "办结"


class CaseService:
    """线程安全的案件服务，全部写操作在同一把锁内串行化。"""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._cases: dict[str, Case] = {}
        self._staff: dict[str, StaffMember] = {}
        self._seq = 0

    # ---- 基础 ----

    def register_staff(self, member: StaffMember) -> None:
        with self._lock:
            self._staff[member.staff_id] = member

    def get_case(self, case_id: str) -> Case:
        with self._lock:
            return self._require_case(case_id)

    def _require_case(self, case_id: str) -> Case:
        try:
            return self._cases[case_id]
        except KeyError:
            raise CaseNotFoundError(f"案件不存在：{case_id}") from None

    def _require_staff(self, staff_id: str) -> StaffMember:
        try:
            return self._staff[staff_id]
        except KeyError:
            raise StaffNotFoundError(f"人员未登记：{staff_id}") from None

    def _require_case_actor(self, case: Case, staff: StaffMember) -> None:
        """协调员或承办组成员才能操作案件。"""
        if staff.role is ActorRole.COORDINATOR:
            return
        if case.assigned_group and staff.group == case.assigned_group:
            return
        raise StaffPermissionError(
            f"{staff.name} 不在承办组内，无权操作 {case.case_id}"
        )

    def _transition(self, case: Case, target: CaseState, at: datetime) -> None:
        allowed = TRANSITIONS[case.state]
        if target not in allowed:
            raise InvalidTransitionError(
                f"{case.case_id} 不能从「{case.state.value}」推进到「{target.value}」"
            )
        case.state = target
        case.updated_at = at

    def _add_action(
        self,
        case: Case,
        *,
        at: datetime,
        actor: str,
        role: ActorRole,
        office: str | None,
        action: ActionType,
        detail: str,
        public_note: str | None = None,
    ) -> None:
        case.actions.append(
            ActionRecord(
                seq=len(case.actions) + 1,
                at=at,
                actor=actor,
                role=role,
                office=office,
                action=action,
                detail=detail,
                public_note=public_note,
            )
        )
        case.updated_at = at

    def _meet_deadline(self, case: Case, kind: str, at: datetime) -> None:
        for deadline in case.deadlines:
            if deadline.kind == kind and deadline.met_at is None:
                deadline.met_at = at
                return

    # ---- 登记与去重 ----

    def intake(
        self,
        *,
        student_id: str,
        issue_type: IssueType,
        summary: str,
        source_office: str,
        consent: ConsentScope,
        channel: str = "现场",
        materials: Iterable[Material] = (),
        at: datetime | None = None,
    ) -> Case:
        """登记求助线索，记录来源并启动办结时限。"""
        at = at or self._now()
        with self._lock:
            self._seq += 1
            case = Case(
                case_id=f"C-{self._seq:04d}",
                student_id=student_id,
                issue_type=issue_type,
                summary=summary,
                consent=consent,
                created_at=at,
                sources=[
                    Source(office=source_office, channel=channel, received_at=at)
                ],
                materials=list(materials),
                deadlines=[
                    Deadline(
                        kind=RESOLUTION_DEADLINE,
                        due_at=at + timedelta(hours=SLA_RESOLUTION_HOURS[issue_type]),
                    )
                ],
            )
            self._cases[case.case_id] = case
            self._add_action(
                case,
                at=at,
                actor=student_id,
                role=ActorRole.STUDENT,
                office=source_office,
                action=ActionType.INTAKE,
                detail=f"经{source_office}登记求助",
                public_note="已收到求助，等待分派",
            )
            return case

    def suggest_duplicates(self, case_id: str) -> list[Case]:
        """同一学生同一问题类型的未关闭线索，供协调员判断是否合并。"""
        with self._lock:
            base = self._require_case(case_id)
            return [
                case
                for case in self._cases.values()
                if case.case_id != case_id
                and case.student_id == base.student_id
                and case.issue_type is base.issue_type
                and case.state is not CaseState.CLOSED
                and case.merged_into is None
            ]

    def merge(
        self,
        primary_id: str,
        duplicate_id: str,
        *,
        actor_id: str,
        note: str = "",
        at: datetime | None = None,
    ) -> Case:
        """把重复线索并入主案件。

        来源与材料全部并入主案（不丢失来源），同意范围取交集保持最小授权，
        重复线索以「已合并」结果关闭并保留回溯指针。
        """
        at = at or self._now()
        with self._lock:
            if primary_id == duplicate_id:
                raise MergeError("不能把案件合并到自身")
            primary = self._require_case(primary_id)
            duplicate = self._require_case(duplicate_id)
            actor = self._require_staff(actor_id)
            if actor.role is not ActorRole.COORDINATOR:
                raise StaffPermissionError("只有项目协调员可以合并线索")
            for case in (primary, duplicate):
                if case.state is CaseState.CLOSED:
                    raise MergeError(f"{case.case_id} 已关闭，不能参与合并")
                if case.merged_into:
                    raise MergeError(f"{case.case_id} 已被合并过")
            primary.sources.extend(duplicate.sources)
            primary.materials.extend(duplicate.materials)
            primary.consent = primary.consent.intersect(duplicate.consent)
            primary.merged_case_ids.append(duplicate.case_id)
            duplicate.merged_into = primary.case_id
            duplicate.outcome = f"已合并至 {primary.case_id}"
            duplicate.state = CaseState.CLOSED
            duplicate.updated_at = at
            offices = "、".join(source.office for source in duplicate.sources)
            self._add_action(
                duplicate,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.MERGE,
                detail=f"{note}并入 {primary.case_id}".strip(),
                public_note=f"重复求助已合并，由 {primary.case_id} 统一跟进",
            )
            self._add_action(
                primary,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.MERGE,
                detail=f"合并重复线索 {duplicate.case_id}（来源：{offices}）。{note}".strip(),
                public_note="重复求助已合并，由同一处理组统一跟进",
            )
            return primary

    # ---- 分派与认领 ----

    def assign(
        self,
        case_id: str,
        *,
        actor_id: str | None = None,
        group_name: str | None = None,
        at: datetime | None = None,
    ) -> Case:
        """分派处理组：默认按问题类型、机构权限与同意范围自动路由。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id) if actor_id else None
            if actor and actor.role is not ActorRole.COORDINATOR:
                raise StaffPermissionError("只有项目协调员可以手工分派")
            if group_name is None:
                group = route(case.issue_type, case.consent)
            else:
                try:
                    group = GROUPS[group_name]
                except KeyError:
                    raise RoutingError(f"处理组不存在：{group_name}") from None
                if not office_permits(group.office, case.issue_type):
                    raise StaffPermissionError(
                        f"{group.office} 无权处理 {case.issue_type.value}"
                    )
                if not case.consent.allows_office(group.office):
                    raise ConsentViolationError(f"学生未同意 {group.office} 受理")
            self._transition(case, CaseState.ASSIGNED, at)
            case.assigned_group = group.name
            case.assigned_office = group.office
            case.referral_target = None
            case.deadlines.append(
                Deadline(
                    kind=RESPONSE_DEADLINE,
                    due_at=at + timedelta(hours=SLA_RESPONSE_HOURS[case.issue_type]),
                )
            )
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id if actor else "system",
                role=actor.role if actor else ActorRole.COORDINATOR,
                office=actor.office if actor else None,
                action=ActionType.ASSIGN,
                detail=f"分派至 {group.name}",
                public_note=f"已分派至{group.office}（{group.name}）",
            )
            return case

    def claim(
        self, case_id: str, *, handler_id: str, at: datetime | None = None
    ) -> Case:
        """认领案件：锁内检查并置位，同一案件只能成功认领一次。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            handler = self._require_staff(handler_id)
            if handler.role is not ActorRole.HANDLER:
                raise StaffPermissionError("只有支持专员可以认领案件")
            if case.claimed_by is not None:
                raise ClaimConflictError(f"{case.case_id} 已被 {case.claimed_by} 认领")
            if case.state is CaseState.REFERRED:
                if case.referral_target != handler.group:
                    raise InvalidTransitionError(
                        f"{case.case_id} 处于转介中，只能由接收组认领"
                    )
            elif case.state is not CaseState.ASSIGNED:
                raise InvalidTransitionError(
                    f"{case.case_id} 当前状态「{case.state.value}」不能认领"
                )
            if handler.group != case.assigned_group:
                raise StaffPermissionError(
                    f"{handler.name} 不属于承办组 {case.assigned_group}"
                )
            if not case.consent.allows_office(handler.office):
                raise ConsentViolationError(f"学生未同意 {handler.office} 受理")
            case.claimed_by = handler.staff_id
            case.referral_target = None
            self._transition(case, CaseState.IN_PROGRESS, at)
            self._meet_deadline(case, RESPONSE_DEADLINE, at)
            self._add_action(
                case,
                at=at,
                actor=handler.staff_id,
                role=handler.role,
                office=handler.office,
                action=ActionType.CLAIM,
                detail=f"{handler.name} 认领案件",
                public_note=f"支持专员已认领，由{handler.office}负责跟进",
            )
            return case

    # ---- 处置与状态推进 ----

    def record_action(
        self,
        case_id: str,
        *,
        actor_id: str,
        note: str,
        public_note: str | None = None,
        at: datetime | None = None,
    ) -> Case:
        """记录处置行动；专员的首次行动同时计为首次响应。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id)
            self._require_case_actor(case, actor)
            if case.state in (CaseState.INTAKE, CaseState.CLOSED):
                raise InvalidTransitionError(
                    f"{case.case_id} 当前状态「{case.state.value}」不能记录行动"
                )
            if actor.role is ActorRole.HANDLER:
                self._meet_deadline(case, RESPONSE_DEADLINE, at)
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.NOTE,
                detail=note,
                public_note=public_note,
            )
            return case

    def refer(
        self,
        case_id: str,
        *,
        actor_id: str,
        target: str,
        external: bool = False,
        expected_by: datetime | None = None,
        note: str = "",
        at: datetime | None = None,
    ) -> Case:
        """转介：内部转介改派处理组，外部转介登记外部依赖。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id)
            self._require_case_actor(case, actor)
            if external:
                case.dependencies.append(
                    ExternalDependency(
                        target=target,
                        created_at=at,
                        expected_by=expected_by or at + timedelta(hours=72),
                    )
                )
                case.referral_target = None
                detail = f"转介外部机构 {target}。{note}".strip()
            else:
                try:
                    group = GROUPS[target]
                except KeyError:
                    raise RoutingError(f"处理组不存在：{target}") from None
                if not office_permits(group.office, case.issue_type):
                    raise StaffPermissionError(
                        f"{group.office} 无权处理 {case.issue_type.value}"
                    )
                if not case.consent.allows_office(group.office):
                    raise ConsentViolationError(f"学生未同意 {group.office} 受理")
                case.assigned_group = group.name
                case.assigned_office = group.office
                case.claimed_by = None
                case.referral_target = group.name
                detail = f"内部转介至 {group.name}。{note}".strip()
            self._transition(case, CaseState.REFERRED, at)
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.REFER,
                detail=detail,
                public_note=f"已转介至{target}，等待受理",
            )
            return case

    def pause(
        self, case_id: str, *, actor_id: str, reason: str, at: datetime | None = None
    ) -> Case:
        """暂停办理，记录来源状态以便恢复。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id)
            self._require_case_actor(case, actor)
            origin = case.state
            self._transition(case, CaseState.PAUSED, at)
            case.paused_from = origin
            case.pause_reason = reason
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.PAUSE,
                detail=f"暂停：{reason}",
                public_note=f"办理暂停：{reason}",
            )
            return case

    def resume(
        self, case_id: str, *, actor_id: str, at: datetime | None = None
    ) -> Case:
        """恢复到暂停前的状态。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id)
            self._require_case_actor(case, actor)
            if case.state is not CaseState.PAUSED or case.paused_from is None:
                raise InvalidTransitionError(f"{case.case_id} 不在暂停中，不能恢复")
            target = case.paused_from
            self._transition(case, target, at)
            case.paused_from = None
            case.pause_reason = None
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.RESUME,
                detail=f"恢复至「{target.value}」",
                public_note="已恢复办理",
            )
            return case

    def escalate(
        self, case_id: str, *, actor_id: str, reason: str, at: datetime | None = None
    ) -> Case:
        """升级案件，提高协调员关注级别。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id)
            if case.state not in ESCALATABLE_STATES:
                raise InvalidTransitionError(
                    f"{case.case_id} 当前状态「{case.state.value}」不能升级"
                )
            case.escalation_level += 1
            case.updated_at = at
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.ESCALATE,
                detail=f"升级至 {case.escalation_level} 级：{reason}",
                public_note="案件已升级，项目协调员介入关注",
            )
            return case

    def close(
        self, case_id: str, *, actor_id: str, outcome: str, at: datetime | None = None
    ) -> Case:
        """关闭案件，必须填写处理结果。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id)
            self._require_case_actor(case, actor)
            if not outcome or not outcome.strip():
                raise DomainError("关闭案件必须填写处理结果")
            self._transition(case, CaseState.CLOSED, at)
            case.outcome = outcome.strip()
            self._meet_deadline(case, RESOLUTION_DEADLINE, at)
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.CLOSE,
                detail=f"关闭：{case.outcome}",
                public_note=f"案件已办结：{case.outcome}",
            )
            return case

    def reopen(
        self, case_id: str, *, actor_id: str, reason: str, at: datetime | None = None
    ) -> Case:
        """重开已关闭案件，回到求助状态重新分派，并重启办结时限。"""
        at = at or self._now()
        with self._lock:
            case = self._require_case(case_id)
            actor = self._require_staff(actor_id)
            if actor.role is not ActorRole.COORDINATOR:
                raise StaffPermissionError("只有项目协调员可以重开案件")
            self._transition(case, CaseState.INTAKE, at)
            case.claimed_by = None
            case.referral_target = None
            case.outcome = None
            case.deadlines.append(
                Deadline(
                    kind=RESOLUTION_DEADLINE,
                    due_at=at + timedelta(hours=SLA_RESOLUTION_HOURS[case.issue_type]),
                )
            )
            self._add_action(
                case,
                at=at,
                actor=actor.staff_id,
                role=actor.role,
                office=actor.office,
                action=ActionType.REOPEN,
                detail=f"重开：{reason}",
                public_note="案件已重新受理，等待再次分派",
            )
            return case

    # ---- 视图 ----

    def student_view(self, case_id: str) -> dict:
        """学生统一进度视图。"""
        with self._lock:
            return _student_view(self._require_case(case_id))

    def student_overview(self, student_id: str) -> list[dict]:
        """学生名下所有未并入他案的案件进度。"""
        with self._lock:
            cases = [
                case
                for case in self._cases.values()
                if case.student_id == student_id and case.merged_into is None
            ]
            cases.sort(key=lambda case: case.created_at)
            return [_student_view(case) for case in cases]

    def coordinator_overview(self, *, at: datetime | None = None) -> list[dict]:
        """协调员总览：逾期优先、升级优先，敏感材料字段已隐藏。"""
        at = at or self._now()
        with self._lock:
            rows = [_coordinator_row(case, at) for case in self._cases.values()]
            rows.sort(
                key=lambda row: (
                    not row["overdue_deadlines"],
                    -row["escalation_level"],
                    row["case_id"],
                )
            )
            return rows

    def materials_for(self, case_id: str, *, viewer_id: str) -> list[dict]:
        """按查看者角色与同意范围返回可见材料。"""
        with self._lock:
            case = self._require_case(case_id)
            if viewer_id == case.student_id:
                return visible_materials(case, role=ActorRole.STUDENT)
            viewer = self._require_staff(viewer_id)
            return visible_materials(case, role=viewer.role, office=viewer.office)
