"""学生与协调员视图。"""
from __future__ import annotations

from datetime import datetime

from .models import Case, CaseState

NEXT_STEP = {
    CaseState.INTAKE: "等待分派处理组",
    CaseState.ASSIGNED: "等待支持专员认领",
    CaseState.IN_PROGRESS: "支持专员处置中",
    CaseState.REFERRED: "已转介，等待接收方受理",
    CaseState.PAUSED: "办理暂停，等待恢复",
    CaseState.CLOSED: "已办结",
}


def student_view(case: Case) -> dict:
    """学生统一进度：一个负责方、一条时间线，不含内部记录。"""
    resolution = next(
        (d for d in case.deadlines if d.kind == "办结" and d.met_at is None), None
    )
    next_step = NEXT_STEP[case.state]
    if case.state is CaseState.PAUSED and case.pause_reason:
        next_step = f"办理暂停：{case.pause_reason}"
    view = {
        "case_id": case.case_id,
        "issue_type": case.issue_type.value,
        "state": case.state.value,
        "responsible_office": case.assigned_office,
        "responsible_group": case.assigned_group,
        "handler": case.claimed_by,
        "escalated": case.escalation_level > 0,
        "next_step": next_step,
        "progress": [
            {"at": action.at.isoformat(), "message": action.public_note}
            for action in case.actions
            if action.public_note
        ],
        "sources": [
            {"office": source.office, "received_at": source.received_at.isoformat()}
            for source in case.sources
        ],
        "merged_from": list(case.merged_case_ids),
        "resolution_due": resolution.due_at.isoformat() if resolution else None,
    }
    if case.state is CaseState.CLOSED:
        view["outcome"] = case.outcome
    return view


def coordinator_row(case: Case, now: datetime) -> dict:
    """协调员行视图：逾期与外部依赖可见，敏感材料字段已隐藏。"""
    overdue = []
    if case.state is not CaseState.CLOSED:
        overdue = [d.kind for d in case.deadlines if d.is_overdue(now)]
    return {
        "case_id": case.case_id,
        "student_id": case.student_id,
        "issue_type": case.issue_type.value,
        "state": case.state.value,
        "assigned_group": case.assigned_group,
        "claimed_by": case.claimed_by,
        "escalation_level": case.escalation_level,
        "overdue_deadlines": overdue,
        "external_dependencies": [
            {
                "target": dep.target,
                "expected_by": dep.expected_by.isoformat(),
                "pending": dep.is_pending(),
                "overdue": dep.is_overdue(now),
            }
            for dep in case.dependencies
        ],
        "materials": [
            {"category": m.category.value, "source_office": m.source_office}
            for m in case.materials
        ],
    }
