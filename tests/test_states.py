"""状态机：转介、暂停、升级、重开按状态推进。"""
from __future__ import annotations

import unittest

from support import COORDINATOR, START, build_service, intake_and_triage

from case_backend import CaseState, InvalidTransitionError
from case_backend.states import TRANSITIONS


class StateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = build_service()

    def test_happy_path_refer_pause_escalate_close_reopen(self) -> None:
        case = intake_and_triage(self.service)
        self.assertEqual(case.state, CaseState.ASSIGNED)

        case = self.service.claim(case.case_id, specialist=_specialist())
        self.assertEqual(case.state, CaseState.IN_PROGRESS)

        case = self.service.refer(
            case.case_id, actor=COORDINATOR, target="企业背景核查中心", description="核查实习资质"
        )
        self.assertEqual(case.state, CaseState.REFERRED)
        dep_id = case.dependencies[0].dependency_id
        case = self.service.resolve_dependency(case.case_id, dep_id, actor=COORDINATOR)
        self.assertEqual(case.state, CaseState.IN_PROGRESS)

        case = self.service.pause(case.case_id, actor=COORDINATOR, reason="等待学生补充材料")
        self.assertEqual(case.state, CaseState.ON_HOLD)
        case = self.service.resume(case.case_id, actor=COORDINATOR)
        self.assertEqual(case.state, CaseState.IN_PROGRESS)

        case = self.service.escalate(case.case_id, actor=COORDINATOR, reason="涉及跨部门争议")
        self.assertEqual(case.state, CaseState.ESCALATED)
        case = self.service.deescalate(case.case_id, actor=COORDINATOR)
        self.assertEqual(case.state, CaseState.IN_PROGRESS)

        case = self.service.close(case.case_id, actor=COORDINATOR, outcome="问题已解决")
        self.assertEqual(case.state, CaseState.CLOSED)
        case = self.service.reopen(case.case_id, actor=COORDINATOR, reason="学生反馈问题复发")
        self.assertEqual(case.state, CaseState.IN_PROGRESS)
        self.assertEqual(case.reopen_count, 1)

    def test_illegal_transitions_are_rejected(self) -> None:
        case = self.service.intake(
            student_id="stu-1",
            issue_type="实习就业",
            source_office="实习办公室",
            description="",
            consent_groups=["intern-office"],
            actor=COORDINATOR,
        )
        with self.assertRaises(InvalidTransitionError):
            self.service.claim(case.case_id, specialist=_specialist())  # 求助状态不能认领
        with self.assertRaises(InvalidTransitionError):
            self.service.resume(case.case_id, actor=COORDINATOR)  # 未暂停不能恢复
        with self.assertRaises(InvalidTransitionError):
            self.service.reopen(case.case_id, actor=COORDINATOR, reason="x")  # 未关闭不能重开

    def test_every_transition_is_recorded_in_history(self) -> None:
        case = intake_and_triage(self.service)
        case = self.service.claim(case.case_id, specialist=_specialist())
        events = [(h.from_state, h.to_state) for h in case.history]
        self.assertEqual(
            events,
            [
                (None, CaseState.INTAKE),
                (CaseState.INTAKE, CaseState.ASSIGNED),
                (CaseState.ASSIGNED, CaseState.IN_PROGRESS),
            ],
        )
        self.assertTrue(all(h.at == START for h in case.history))

    def test_transition_table_covers_contract_states(self) -> None:
        covered = {state for mapping in TRANSITIONS.values() for state in mapping}
        covered |= set(CaseState)
        self.assertEqual(covered, set(CaseState))


def _specialist():
    from support import INTERN_SPECIALIST

    return INTERN_SPECIALIST


if __name__ == "__main__":
    unittest.main()
