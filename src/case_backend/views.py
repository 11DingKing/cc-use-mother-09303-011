"""角色化视图：按资料可见边界输出学生统一进度与协调员概览。"""
from __future__ import annotations

from datetime import datetime

from .models import ActionKind, Case, CaseState
from .repository import InMemoryCaseRepository
from .routing import RoutingTable


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _past(due: datetime | None, now: datetime) -> bool:
    return due is not None and now > due


def _group_name(routing: RoutingTable, group_id: str | None) -> str:
    group = routing.groups.get(group_id) if group_id else None
    return group.name if group else "待分派"


def student_progress(
    repo: InMemoryCaseRepository, routing: RoutingTable, student_id: str, *, now: datetime
) -> dict:
    """学生视角的统一进度。

    重复线索合并后只呈现主案件（来源留痕在 merged_sources）；
    时间线只含 student_visible 的行动，并按 material_key 去重
    重复的索取材料请求；内部备注与敏感字段不出视图。
    """
    cases = repo.find_by_student(student_id)
    by_id = {c.case_id: c for c in cases}
    items = []
    primaries = (c for c in cases if c.merged_into is None)
    for case in sorted(primaries, key=lambda c: c.created_at):
        items.append(
            {
                "case_id": case.case_id,
                "issue_type": case.issue_type,
                "state": case.state.value,
                "responsible_group": _group_name(routing, case.group_id),
                "claimed": case.assignee_id is not None,
                "response_due_at": _iso(case.response_due_at),
                "resolution_due_at": _iso(case.resolution_due_at),
                "resolution_overdue": case.open and _past(case.resolution_due_at, now),
                "merged_sources": [
                    {"case_id": s.case_id, "source_office": s.source_office}
                    for s in case.merged_sources
                ],
                "outcome": case.student_summary or None,
                "timeline": _student_timeline(case, by_id),
            }
        )
    return {"student_id": student_id, "case_count": len(items), "cases": items}


def _student_timeline(case: Case, by_id: dict[str, Case]) -> list[dict]:
    records = list(case.actions)
    for source in case.merged_sources:
        origin = by_id.get(source.case_id)
        if origin is not None:
            records.extend(origin.actions)
    visible = [a for a in records if a.student_visible]
    visible.sort(key=lambda a: (a.created_at, a.action_id))
    timeline: list[dict] = []
    seen_materials: dict[str, dict] = {}
    for action in visible:
        key = action.material_key if action.kind is ActionKind.REQUEST_MATERIAL else None
        if key is not None and key in seen_materials:
            seen_materials[key]["merged_duplicates"] += 1
            continue
        origin = by_id.get(action.case_id)
        entry = {
            "at": _iso(action.created_at),
            "kind": action.kind.value,
            "detail": action.detail,
            "source_office": origin.source_office if origin else None,
        }
        if key is not None:
            entry["material_key"] = key
            entry["merged_duplicates"] = 0
            seen_materials[key] = entry
        timeline.append(entry)
    return timeline


def coordinator_overview(
    repo: InMemoryCaseRepository, routing: RoutingTable, *, now: datetime
) -> dict:
    """协调员视角的调度概览。

    只输出调度所需字段（状态、处理组、时限、逾期、外部依赖），
    不含案件描述、行动内容等与学生支持无关的敏感字段。
    """
    items = []
    for case in repo.list_all():
        if case.state is CaseState.CLOSED:
            continue
        pending = [d for d in case.dependencies if d.pending]
        items.append(
            {
                "case_id": case.case_id,
                "student_id": case.student_id,
                "issue_type": case.issue_type,
                "state": case.state.value,
                "group_id": case.group_id,
                "assignee_id": case.assignee_id,
                "response_due_at": _iso(case.response_due_at),
                "resolution_due_at": _iso(case.resolution_due_at),
                "response_overdue": case.assignee_id is None and _past(case.response_due_at, now),
                "resolution_overdue": _past(case.resolution_due_at, now),
                "reopen_count": case.reopen_count,
                "merged_source_count": len(case.merged_sources),
                "pending_dependencies": [
                    {
                        "dependency_id": d.dependency_id,
                        "target": d.target,
                        "expected_at": _iso(d.expected_at),
                        "late": _past(d.expected_at, now),
                    }
                    for d in pending
                ],
            }
        )
    items.sort(key=lambda i: (not i["resolution_overdue"], i["case_id"]))
    return {
        "generated_at": _iso(now),
        "overdue_case_ids": [
            i["case_id"] for i in items if i["response_overdue"] or i["resolution_overdue"]
        ],
        "external_dependency_case_ids": [i["case_id"] for i in items if i["pending_dependencies"]],
        "cases": items,
    }


def case_to_dict(case: Case) -> dict:
    """工作人员视角的完整案件序列化（仅供员工接口使用）。"""
    return {
        "case_id": case.case_id,
        "student_id": case.student_id,
        "issue_type": case.issue_type,
        "source_office": case.source_office,
        "description": case.description,
        "state": case.state.value,
        "consent_groups": sorted(case.consent_groups),
        "group_id": case.group_id,
        "assignee_id": case.assignee_id,
        "created_at": _iso(case.created_at),
        "response_due_at": _iso(case.response_due_at),
        "resolution_due_at": _iso(case.resolution_due_at),
        "closed_at": _iso(case.closed_at),
        "close_outcome": case.close_outcome,
        "close_reason": case.close_reason,
        "student_summary": case.student_summary,
        "merged_into": case.merged_into,
        "merged_sources": [
            {
                "case_id": s.case_id,
                "source_office": s.source_office,
                "intake_at": _iso(s.intake_at),
                "merged_at": _iso(s.merged_at),
            }
            for s in case.merged_sources
        ],
        "actions": [
            {
                "action_id": a.action_id,
                "kind": a.kind.value,
                "detail": a.detail,
                "actor_id": a.actor.actor_id,
                "at": _iso(a.created_at),
                "student_visible": a.student_visible,
                "material_key": a.material_key,
                "sensitivity": a.sensitivity.value,
            }
            for a in case.actions
        ],
        "dependencies": [
            {
                "dependency_id": d.dependency_id,
                "target": d.target,
                "description": d.description,
                "expected_at": _iso(d.expected_at),
                "resolved_at": _iso(d.resolved_at),
                "pending": d.pending,
            }
            for d in case.dependencies
        ],
        "history": [
            {
                "from": h.from_state.value if h.from_state else None,
                "to": h.to_state.value,
                "actor_id": h.actor.actor_id,
                "at": _iso(h.at),
                "reason": h.reason,
            }
            for h in case.history
        ],
        "reopen_count": case.reopen_count,
        "version": case.version,
    }
