"""案件后端回归测试：去重合并、同意范围、服务时限、并发认领、可见边界。"""
from __future__ import annotations

import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from case_backend import (
    ActorRole,
    CaseService,
    CaseState,
    ClaimConflictError,
    ConsentScope,
    ConsentViolationError,
    DomainError,
    InvalidTransitionError,
    IssueType,
    Material,
    MaterialCategory,
    MergeError,
    RoutingError,
    StaffMember,
    StaffPermissionError,
)

START = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kwargs) -> None:
        self.t += timedelta(**kwargs)


def make_consent(offices, health_offices=()) -> ConsentScope:
    offices = frozenset(offices)
    return ConsentScope(
        offices=offices,
        materials={
            MaterialCategory.IDENTITY: offices,
            MaterialCategory.ACADEMIC_RECORD: offices,
            MaterialCategory.CONTACT: offices,
            MaterialCategory.HEALTH: frozenset(health_offices),
        },
    )


class CaseServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock(START)
        self.service = CaseService(now=self.clock)
        self.service.register_staff(
            StaffMember("coord-1", "陈协调", "支持中心", "支持中心-协调组", ActorRole.COORDINATOR)
        )
        self.service.register_staff(
            StaffMember("handler-intern", "李实习", "实习办公室", "实习办公室-就业组")
        )
        self.service.register_staff(
            StaffMember("handler-intern-2", "周就业", "实习办公室", "实习办公室-就业组")
        )
        self.service.register_staff(
            StaffMember("handler-academic", "王教务", "教务办公室", "教务办公室-学籍组")
        )
        self.service.register_staff(
            StaffMember("handler-visa", "赵签证", "国际事务中心", "国际事务中心-签证组")
        )
        self.service.register_staff(
            StaffMember("handler-wellbeing", "孙心理", "心理支持中心", "心理支持中心-关怀组")
        )
        self.consent = make_consent({"实习办公室", "教务办公室", "国际事务中心"})

    def intake(self, office="实习办公室", **overrides):
        params = {
            "student_id": "S-1001",
            "issue_type": IssueType.INTERNSHIP,
            "summary": "实习证明与学籍冲突",
            "source_office": office,
            "consent": self.consent,
        }
        params.update(overrides)
        return self.service.intake(**params)

    # ---- 登记、去重与合并 ----

    def test_intake_records_source_and_resolution_deadline(self) -> None:
        case = self.intake()
        self.assertEqual(case.state, CaseState.INTAKE)
        self.assertEqual([s.office for s in case.sources], ["实习办公室"])
        deadline = case.deadlines[0]
        self.assertEqual(deadline.kind, "办结")
        self.assertEqual(deadline.due_at, START + timedelta(hours=120))

    def test_duplicate_leads_merge_without_losing_sources(self) -> None:
        first = self.intake(office="实习办公室")
        second = self.intake(office="教务办公室")
        self.assertEqual(
            {c.case_id for c in self.service.suggest_duplicates(first.case_id)},
            {second.case_id},
        )
        merged = self.service.merge(
            first.case_id, second.case_id, actor_id="coord-1", note="同一学生重复求助"
        )
        self.assertEqual([s.office for s in merged.sources], ["实习办公室", "教务办公室"])
        self.assertEqual(merged.merged_case_ids, [second.case_id])
        duplicate = self.service.get_case(second.case_id)
        self.assertEqual(duplicate.state, CaseState.CLOSED)
        self.assertEqual(duplicate.merged_into, first.case_id)
        self.assertEqual(duplicate.outcome, f"已合并至 {first.case_id}")

    def test_merge_intersects_consent_scope(self) -> None:
        first = self.intake(office="实习办公室")
        narrow = make_consent({"实习办公室"})
        second = self.intake(office="实习办公室", consent=narrow)
        merged = self.service.merge(first.case_id, second.case_id, actor_id="coord-1")
        self.assertEqual(merged.consent.offices, frozenset({"实习办公室"}))

    def test_merge_rejects_invalid_pairs(self) -> None:
        first = self.intake()
        second = self.intake()
        with self.assertRaises(MergeError):
            self.service.merge(first.case_id, first.case_id, actor_id="coord-1")
        with self.assertRaises(StaffPermissionError):
            self.service.merge(first.case_id, second.case_id, actor_id="handler-intern")
        self.service.merge(first.case_id, second.case_id, actor_id="coord-1")
        third = self.intake()
        with self.assertRaises(MergeError):
            self.service.merge(third.case_id, second.case_id, actor_id="coord-1")

    # ---- 分派：问题类型 × 机构权限 × 同意范围 ----

    def test_routing_prefers_specialized_group(self) -> None:
        case = self.intake()
        self.service.assign(case.case_id)
        self.assertEqual(case.assigned_group, "实习办公室-就业组")

    def test_routing_falls_back_when_consent_excludes_office(self) -> None:
        consent = make_consent({"国际事务中心"})
        case = self.intake(consent=consent)
        self.service.assign(case.case_id)
        self.assertEqual(case.assigned_group, "国际事务中心-综合组")

    def test_routing_fails_without_permitted_and_consented_group(self) -> None:
        consent = make_consent({"教务办公室"})
        case = self.intake(issue_type=IssueType.VISA, consent=consent)
        with self.assertRaises(RoutingError):
            self.service.assign(case.case_id)

    def test_manual_assign_checks_permission_and_consent(self) -> None:
        case = self.intake()
        with self.assertRaises(StaffPermissionError):
            self.service.assign(
                case.case_id, actor_id="coord-1", group_name="心理支持中心-关怀组"
            )
        consent = make_consent({"国际事务中心"})
        other = self.intake(consent=consent)
        with self.assertRaises(ConsentViolationError):
            self.service.assign(
                other.case_id, actor_id="coord-1", group_name="实习办公室-就业组"
            )

    # ---- 认领：只能成功一次 ----

    def assigned_case(self):
        case = self.intake()
        self.service.assign(case.case_id)
        return case

    def test_claim_only_succeeds_once(self) -> None:
        case = self.assigned_case()
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.assertEqual(case.state, CaseState.IN_PROGRESS)
        with self.assertRaises(ClaimConflictError):
            self.service.claim(case.case_id, handler_id="handler-intern-2")

    def test_claim_is_atomic_under_concurrency(self) -> None:
        case = self.assigned_case()
        barrier = threading.Barrier(2)
        results, errors = [], []

        def attempt(handler_id: str) -> None:
            barrier.wait()
            try:
                self.service.claim(case.case_id, handler_id=handler_id)
                results.append(handler_id)
            except ClaimConflictError:
                errors.append(handler_id)

        threads = [
            threading.Thread(target=attempt, args=(handler,))
            for handler in ("handler-intern", "handler-intern-2")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(case.claimed_by, results[0])

    def test_claim_requires_assigned_group_and_consent(self) -> None:
        case = self.assigned_case()
        with self.assertRaises(StaffPermissionError):
            self.service.claim(case.case_id, handler_id="handler-academic")

    def test_claim_blocked_when_merge_shrinks_consent(self) -> None:
        case = self.assigned_case()
        narrow = self.intake(consent=make_consent({"国际事务中心"}))
        self.service.merge(case.case_id, narrow.case_id, actor_id="coord-1")
        with self.assertRaises(ConsentViolationError):
            self.service.claim(case.case_id, handler_id="handler-intern")

    def test_claim_before_assign_is_rejected(self) -> None:
        case = self.intake()
        with self.assertRaises(InvalidTransitionError):
            self.service.claim(case.case_id, handler_id="handler-intern")

    # ---- 状态推进：转介、暂停、升级、重开 ----

    def test_pause_and_resume_returns_to_previous_state(self) -> None:
        case = self.assigned_case()
        self.service.pause(case.case_id, actor_id="coord-1", reason="等待学生补充材料")
        self.assertEqual(case.state, CaseState.PAUSED)
        self.service.resume(case.case_id, actor_id="coord-1")
        self.assertEqual(case.state, CaseState.ASSIGNED)
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.pause(case.case_id, actor_id="handler-intern", reason="等待外部回复")
        self.service.resume(case.case_id, actor_id="handler-intern")
        self.assertEqual(case.state, CaseState.IN_PROGRESS)

    def test_resume_without_pause_is_rejected(self) -> None:
        case = self.assigned_case()
        with self.assertRaises(InvalidTransitionError):
            self.service.resume(case.case_id, actor_id="coord-1")

    def test_internal_referral_reroutes_to_target_group(self) -> None:
        case = self.assigned_case()
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.refer(
            case.case_id, actor_id="handler-intern", target="国际事务中心-综合组"
        )
        self.assertEqual(case.state, CaseState.REFERRED)
        self.assertIsNone(case.claimed_by)
        with self.assertRaises(InvalidTransitionError):
            self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.register_staff(
            StaffMember("handler-general", "钱综合", "国际事务中心", "国际事务中心-综合组")
        )
        self.service.claim(case.case_id, handler_id="handler-general")
        self.assertEqual(case.state, CaseState.IN_PROGRESS)

    def test_external_referral_registers_dependency(self) -> None:
        case = self.assigned_case()
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.refer(
            case.case_id,
            actor_id="handler-intern",
            target="校外职业机构",
            external=True,
            expected_by=START + timedelta(hours=24),
        )
        self.assertEqual(len(case.dependencies), 1)
        self.assertTrue(case.dependencies[0].is_pending())
        self.clock.advance(hours=25)
        rows = self.service.coordinator_overview()
        dep = rows[0]["external_dependencies"][0]
        self.assertTrue(dep["pending"])
        self.assertTrue(dep["overdue"])

    def test_escalate_tracks_level_and_state(self) -> None:
        case = self.assigned_case()
        self.service.escalate(case.case_id, actor_id="coord-1", reason="学生多次催促")
        self.assertEqual(case.escalation_level, 1)
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.escalate(case.case_id, actor_id="handler-intern", reason="涉及多部门")
        self.assertEqual(case.escalation_level, 2)

    def test_close_requires_outcome_and_valid_state(self) -> None:
        case = self.intake()
        with self.assertRaises(InvalidTransitionError):
            self.service.close(case.case_id, actor_id="coord-1", outcome="无效求助")
        self.service.assign(case.case_id)
        with self.assertRaises(DomainError):
            self.service.close(case.case_id, actor_id="coord-1", outcome="  ")
        self.service.close(case.case_id, actor_id="coord-1", outcome="重复求助，无需处理")
        self.assertEqual(case.state, CaseState.CLOSED)
        resolution = next(d for d in case.deadlines if d.kind == "办结")
        self.assertIsNotNone(resolution.met_at)

    def test_reopen_restarts_cycle_and_deadline(self) -> None:
        case = self.assigned_case()
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.close(case.case_id, actor_id="handler-intern", outcome="已答复")
        self.clock.advance(days=2)
        self.service.reopen(case.case_id, actor_id="coord-1", reason="学生补充新情况")
        self.assertEqual(case.state, CaseState.INTAKE)
        self.assertIsNone(case.claimed_by)
        self.assertIsNone(case.outcome)
        unmet = [d for d in case.deadlines if d.met_at is None]
        self.assertEqual(len(unmet), 1)
        self.assertEqual(unmet[0].due_at, self.clock.t + timedelta(hours=120))
        self.service.assign(case.case_id)
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.close(case.case_id, actor_id="handler-intern", outcome="二次办结")

    def test_closed_case_cannot_escalate_or_pause(self) -> None:
        case = self.assigned_case()
        self.service.close(case.case_id, actor_id="coord-1", outcome="已办结")
        with self.assertRaises(InvalidTransitionError):
            self.service.escalate(case.case_id, actor_id="coord-1", reason="尝试")
        with self.assertRaises(InvalidTransitionError):
            self.service.pause(case.case_id, actor_id="coord-1", reason="尝试")

    # ---- 服务时限与协调员视图 ----

    def test_overdue_deadlines_surface_in_coordinator_overview(self) -> None:
        case = self.intake(issue_type=IssueType.WELLBEING, consent=make_consent({"心理支持中心"}))
        self.service.assign(case.case_id)
        self.clock.advance(hours=5)
        rows = self.service.coordinator_overview()
        self.assertEqual(rows[0]["overdue_deadlines"], ["首次响应"])
        self.clock.advance(hours=50)
        rows = self.service.coordinator_overview()
        self.assertEqual(rows[0]["overdue_deadlines"], ["办结", "首次响应"])

    def test_coordinator_overview_hides_sensitive_material_fields(self) -> None:
        material = Material(
            material_id="M-1",
            category=MaterialCategory.HEALTH,
            title="心理咨询记录",
            content="敏感内容",
            source_office="实习办公室",
            uploaded_at=START,
        )
        self.intake(materials=[material])
        rows = self.service.coordinator_overview()
        self.assertEqual(
            rows[0]["materials"],
            [{"category": "医疗健康", "source_office": "实习办公室"}],
        )

    def test_coordinator_overview_sorts_overdue_first(self) -> None:
        overdue_case = self.intake(issue_type=IssueType.WELLBEING, consent=make_consent({"心理支持中心"}))
        self.service.assign(overdue_case.case_id)
        normal = self.intake()
        self.service.assign(normal.case_id)
        self.clock.advance(hours=5)
        rows = self.service.coordinator_overview()
        self.assertEqual(rows[0]["case_id"], overdue_case.case_id)

    # ---- 资料可见边界 ----

    def test_material_visibility_follows_role_and_consent(self) -> None:
        consent = make_consent({"实习办公室"}, health_offices={"心理支持中心"})
        materials = [
            Material("M-1", MaterialCategory.IDENTITY, "护照", "E12345", "实习办公室", START),
            Material("M-2", MaterialCategory.HEALTH, "心理记录", "敏感", "实习办公室", START),
        ]
        case = self.intake(consent=consent, materials=materials)
        self.service.assign(case.case_id)
        student_materials = self.service.materials_for(case.case_id, viewer_id="S-1001")
        self.assertEqual(len(student_materials), 2)
        handler_materials = self.service.materials_for(case.case_id, viewer_id="handler-intern")
        self.assertEqual([m["material_id"] for m in handler_materials], ["M-1"])
        coordinator_materials = self.service.materials_for(case.case_id, viewer_id="coord-1")
        self.assertNotIn("content", coordinator_materials[0])
        self.assertEqual(coordinator_materials[1]["category"], "医疗健康")

    # ---- 学生统一进度 ----

    def test_student_view_shows_single_responsible_party(self) -> None:
        first = self.intake(office="实习办公室")
        second = self.intake(office="教务办公室")
        self.service.merge(first.case_id, second.case_id, actor_id="coord-1")
        self.service.assign(first.case_id)
        self.service.claim(first.case_id, handler_id="handler-intern")
        view = self.service.student_view(first.case_id)
        self.assertEqual(view["responsible_office"], "实习办公室")
        self.assertEqual(view["handler"], "handler-intern")
        self.assertEqual(len(view["sources"]), 2)
        self.assertEqual(view["merged_from"], [second.case_id])
        messages = [item["message"] for item in view["progress"]]
        self.assertTrue(any("已分派" in message for message in messages))
        self.assertTrue(any("已认领" in message for message in messages))

    def test_student_view_hides_internal_notes(self) -> None:
        case = self.assigned_case()
        self.service.claim(case.case_id, handler_id="handler-intern")
        self.service.record_action(
            case.case_id, actor_id="handler-intern", note="内部：学生情绪不稳定，注意措辞"
        )
        self.service.record_action(
            case.case_id,
            actor_id="handler-intern",
            note="已联系企业核实岗位",
            public_note="已联系实习单位核实情况",
        )
        view = self.service.student_view(case.case_id)
        messages = [item["message"] for item in view["progress"]]
        self.assertEqual(messages[-1], "已联系实习单位核实情况")
        self.assertFalse(any("情绪" in message for message in messages))

    def test_student_overview_excludes_merged_duplicates(self) -> None:
        first = self.intake(office="实习办公室")
        second = self.intake(office="教务办公室")
        self.service.merge(first.case_id, second.case_id, actor_id="coord-1")
        overview = self.service.student_overview("S-1001")
        self.assertEqual([item["case_id"] for item in overview], [first.case_id])


if __name__ == "__main__":
    unittest.main()
