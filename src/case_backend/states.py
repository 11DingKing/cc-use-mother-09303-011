"""案件状态机：转介、暂停、升级、重开均按状态推进。"""
from __future__ import annotations

from datetime import datetime

from .errors import InvalidTransitionError
from .models import Actor, Case, CaseState, StateChange

# 事件 -> {允许的起始状态: 目标状态}
TRANSITIONS: dict[str, dict[CaseState, CaseState]] = {
    "triage": {CaseState.INTAKE: CaseState.ASSIGNED},
    "claim": {CaseState.ASSIGNED: CaseState.IN_PROGRESS},
    "refer": {CaseState.IN_PROGRESS: CaseState.REFERRED},
    "resolve": {CaseState.REFERRED: CaseState.IN_PROGRESS},
    "pause": {CaseState.IN_PROGRESS: CaseState.ON_HOLD},
    "resume": {CaseState.ON_HOLD: CaseState.IN_PROGRESS},
    "escalate": {CaseState.IN_PROGRESS: CaseState.ESCALATED},
    "deescalate": {CaseState.ESCALATED: CaseState.IN_PROGRESS},
    "close": {
        CaseState.INTAKE: CaseState.CLOSED,
        CaseState.ASSIGNED: CaseState.CLOSED,
        CaseState.IN_PROGRESS: CaseState.CLOSED,
        CaseState.REFERRED: CaseState.CLOSED,
        CaseState.ON_HOLD: CaseState.CLOSED,
        CaseState.ESCALATED: CaseState.CLOSED,
    },
    "reopen": {CaseState.CLOSED: CaseState.IN_PROGRESS},
}


def apply_transition(case: Case, event: str, actor: Actor, at: datetime, reason: str = "") -> None:
    """校验并执行一次状态推进，同时把轨迹写入案件历史。"""
    targets = TRANSITIONS.get(event)
    if targets is None:
        raise InvalidTransitionError(f"未知事件：{event}")
    target = targets.get(case.state)
    if target is None:
        raise InvalidTransitionError(
            f"案件 {case.case_id} 不能从「{case.state.value}」执行「{event}」"
        )
    case.history.append(StateChange(case.state, target, actor, at, reason))
    case.state = target
