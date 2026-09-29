"""案件服务：认领、行动、外部依赖、关闭与重开。"""
from __future__ import annotations

import unittest

from support import (
    ACADEMIC_SPECIALIST,
    COORDINATOR,
    INTERN_SPECIALIST,
    STUDENT,
    build_service,
    intake_and_triage,
)

from case_backend import (
    ActionKind,
    CaseState,
    ClaimConflictError,
    InvalidTransitionError,
    PendingDependencyError,
    PermissionDeniedError,
    Sensitivity,
    ValidationError,
)


class ClaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = build_service()
        self.case = intake_and_triage(self.service)

    def test_claim_moves_to_in_progress(self) -> None:
        case = self.service.claim(self.case.case_id, specialist=INTERN_SPECIALIST)
        self.assertEqual(case.state, CaseState.IN_PROGRESS)
        self.assertEqual(case.assignee_id, "spec-intern")

    def test_second_claim_fails(self) -> None:
        self.service.claim(self.case.case_id, specialist=INTERN_SPECIALIST)
        with self.assertRaises(ClaimConflictError):
            self.service.claim(self.case.case_id, specialist=INTERN_SPECIALIST)

    def test_claim_requires_assigned_group(self) -> None:
        with self.assertRaises(PermissionDeniedError):
            self.service.claim(self.case.case_id, specialist=ACADEMIC_SPECIALIST)

    def test_student_cannot_claim(self) -> None:
        with self.assertRaises(PermissionDeniedError):
            self.service.claim(self.case.case_id, specialist=STUDENT)


class ActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = build_service()
        self.case = intake_and_triage(self.service)
        self.service.claim(self.case.case_id, specialist=INTERN_SPECIALIST)

    def test_record_action_with_visibility_boundary(self) -> None:
        case = self.service.record_action(
            self.case.case_id,
            actor=INTERN_SPECIALIST,
            kind=ActionKind.REQUEST_MATERIAL,
            detail="请提供护照复印件",
            student_visible=True,
            material_key="护照复印件",
            sensitivity=Sensitivity.HIGH,
        )
        (action,) = case.actions
        self.assertEqual(action.material_key, "护照复印件")
        self.assertTrue(action.student_visible)
        self.assertEqual(action.sensitivity, Sensitivity.HIGH)

    def test_other_group_specialist_cannot_act(self) -> None:
        with self.assertRaises(PermissionDeniedError):
            self.service.record_action(
                self.case.case_id, actor=ACADEMIC_SPECIALIST, kind="内部备注", detail="x"
            )

    def test_closed_case_rejects_new_actions(self) -> None:
        self.service.close(self.case.case_id, actor=INTERN_SPECIALIST, outcome="已解决")
        with self.assertRaises(InvalidTransitionError):
            self.service.record_action(
                self.case.case_id, actor=INTERN_SPECIALIST, kind="内部备注", detail="x"
            )


class DependencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = build_service()
        self.case = intake_and_triage(self.service)
        self.service.claim(self.case.case_id, specialist=INTERN_SPECIALIST)

    def test_close_blocked_by_pending_dependency(self) -> None:
        case = self.service.refer(
            self.case.case_id, actor=INTERN_SPECIALIST, target="企业背景核查中心", description="核查"
        )
        with self.assertRaises(PendingDependencyError):
            self.service.close(self.case.case_id, actor=INTERN_SPECIALIST, outcome="办结")
        case = self.service.resolve_dependency(
            self.case.case_id, case.dependencies[0].dependency_id, actor=INTERN_SPECIALIST
        )
        self.assertEqual(case.state, CaseState.IN_PROGRESS)
        case = self.service.close(self.case.case_id, actor=INTERN_SPECIALIST, outcome="办结")
        self.assertEqual(case.state, CaseState.CLOSED)
        self.assertEqual(case.close_outcome, "办结")


class CloseReopenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = build_service()
        self.case = intake_and_triage(self.service)
        self.service.claim(self.case.case_id, specialist=INTERN_SPECIALIST)

    def test_close_requires_outcome(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.close(self.case.case_id, actor=INTERN_SPECIALIST, outcome="")

    def test_reopen_resets_closure_and_extends_deadline(self) -> None:
        case = self.service.close(
            self.case.case_id, actor=INTERN_SPECIALIST, outcome="已解决", student_summary="办好了"
        )
        original_due = case.resolution_due_at
        self.clock.advance(days=10)
        case = self.service.reopen(self.case.case_id, actor=COORDINATOR, reason="问题复发")
        self.assertIsNone(case.closed_at)
        self.assertIsNone(case.close_outcome)
        self.assertEqual(case.reopen_count, 1)
        self.assertGreater(case.resolution_due_at, original_due)

    def test_pause_requires_reason(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.pause(self.case.case_id, actor=INTERN_SPECIALIST, reason="")


if __name__ == "__main__":
    unittest.main()
