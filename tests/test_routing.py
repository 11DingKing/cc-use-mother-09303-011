"""分派路由：问题类型 + 机构权限 + 学生同意范围。"""
from __future__ import annotations

import unittest

from support import COORDINATOR, STUDENT, build_service

from case_backend import ConsentError, RoutingError, build_default_routing


class RoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.routing = build_default_routing()
        self.service, _ = build_service()

    def test_route_prefers_specialized_office(self) -> None:
        group = self.routing.route("实习就业", frozenset({"intern-office", "support-center"}))
        self.assertEqual(group.group_id, "intern-office")

    def test_route_falls_back_within_consent(self) -> None:
        group = self.routing.route("实习就业", frozenset({"support-center"}))
        self.assertEqual(group.group_id, "support-center")

    def test_route_respects_consent_scope(self) -> None:
        with self.assertRaises(ConsentError):
            self.routing.route("实习就业", frozenset({"academic-office"}))

    def test_unknown_issue_type_rejected(self) -> None:
        with self.assertRaises(RoutingError):
            self.routing.route("未知类型", frozenset({"support-center"}))

    def test_triage_blocked_until_student_grants_consent(self) -> None:
        case = self.service.intake(
            student_id="stu-1",
            issue_type="实习就业",
            source_office="实习办公室",
            description="",
            consent_groups=["academic-office"],  # 只同意了教务，承接不了实习
            actor=STUDENT,
        )
        with self.assertRaises(ConsentError):
            self.service.triage(case.case_id, actor=COORDINATOR)
        self.service.grant_consent(case.case_id, "intern-office", actor=STUDENT)
        case = self.service.triage(case.case_id, actor=COORDINATOR)
        self.assertEqual(case.group_id, "intern-office")

    def test_triage_sets_sla_deadlines_from_intake_time(self) -> None:
        case = self.service.intake(
            student_id="stu-1",
            issue_type="实习就业",
            source_office="实习办公室",
            description="",
            consent_groups=["intern-office"],
            actor=STUDENT,
        )
        case = self.service.triage(case.case_id, actor=COORDINATOR)
        self.assertEqual((case.response_due_at - case.created_at).total_seconds(), 24 * 3600)
        self.assertEqual((case.resolution_due_at - case.created_at).total_seconds(), 120 * 3600)


if __name__ == "__main__":
    unittest.main()
