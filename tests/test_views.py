"""角色化视图：学生统一进度与协调员概览的资料可见边界。"""
from __future__ import annotations

import json
import unittest

from support import (
    ACADEMIC_SPECIALIST,
    COORDINATOR,
    INTERN_SPECIALIST,
    build_service,
    intake_and_triage,
)

from case_backend import coordinator_overview, student_progress


def _scenario(service):
    """同一学生向两个办公室求助，两边都索取相同隐私材料，随后合并。"""
    lead_a = intake_and_triage(service, issue_type="实习就业", source_office="实习办公室")
    lead_b = intake_and_triage(service, issue_type="学业课程", source_office="教务办公室")
    service.claim(lead_a.case_id, specialist=INTERN_SPECIALIST)
    service.claim(lead_b.case_id, specialist=ACADEMIC_SPECIALIST)
    for case_id, actor in ((lead_a.case_id, INTERN_SPECIALIST), (lead_b.case_id, ACADEMIC_SPECIALIST)):
        service.record_action(
            case_id,
            actor=actor,
            kind="索取材料",
            detail="请提供护照复印件",
            student_visible=True,
            material_key="护照复印件",
        )
    service.record_action(
        lead_a.case_id, actor=INTERN_SPECIALIST, kind="内部备注", detail="内部：待核实推荐信真伪"
    )
    service.merge(lead_a.case_id, lead_b.case_id, actor=COORDINATOR)
    return lead_a, lead_b


class StudentProgressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = build_service()
        self.lead_a, self.lead_b = _scenario(self.service)

    def test_student_sees_one_unified_case(self) -> None:
        progress = student_progress(
            self.service.repo, self.service.routing, "stu-1", now=self.clock()
        )
        self.assertEqual(progress["case_count"], 1)
        (item,) = progress["cases"]
        self.assertEqual(item["case_id"], self.lead_a.case_id)
        self.assertEqual(item["responsible_group"], "实习办公室")
        self.assertTrue(item["claimed"])
        self.assertEqual(
            item["merged_sources"],
            [{"case_id": self.lead_b.case_id, "source_office": "教务办公室"}],
        )

    def test_duplicate_material_requests_are_deduped(self) -> None:
        progress = student_progress(
            self.service.repo, self.service.routing, "stu-1", now=self.clock()
        )
        (item,) = progress["cases"]
        requests = [t for t in item["timeline"] if t["kind"] == "索取材料"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["material_key"], "护照复印件")
        self.assertEqual(requests[0]["merged_duplicates"], 1)

    def test_internal_notes_never_reach_student_view(self) -> None:
        progress = student_progress(
            self.service.repo, self.service.routing, "stu-1", now=self.clock()
        )
        self.assertNotIn("内部：待核实推荐信真伪", json.dumps(progress, ensure_ascii=False))
        self.assertNotIn("assignee_id", json.dumps(progress, ensure_ascii=False))


class CoordinatorOverviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = build_service()
        self.lead_a, self.lead_b = _scenario(self.service)

    def test_overview_flags_overdue_and_external_dependencies(self) -> None:
        self.service.refer(
            self.lead_a.case_id,
            actor=INTERN_SPECIALIST,
            target="企业背景核查中心",
            description="核查实习资质",
            expected_at=self.clock().replace(hour=12),
        )
        self.clock.advance(days=6)  # 超过 120 小时办结时限，且依赖已过期
        overview = coordinator_overview(self.service.repo, self.service.routing, now=self.clock())

        self.assertEqual(overview["overdue_case_ids"], [self.lead_a.case_id])
        self.assertEqual(overview["external_dependency_case_ids"], [self.lead_a.case_id])
        (item,) = overview["cases"]
        self.assertTrue(item["resolution_overdue"])
        self.assertEqual(item["state"], "转介")
        (dep,) = item["pending_dependencies"]
        self.assertEqual(dep["target"], "企业背景核查中心")
        self.assertTrue(dep["late"])

    def test_overview_does_not_leak_sensitive_fields(self) -> None:
        overview = coordinator_overview(self.service.repo, self.service.routing, now=self.clock())
        payload = json.dumps(overview, ensure_ascii=False)
        self.assertNotIn("内部：待核实推荐信真伪", payload)
        self.assertNotIn("请提供护照复印件", payload)
        self.assertNotIn("description", payload)
        self.assertNotIn("actions", payload)
        self.assertNotIn("timeline", payload)

    def test_response_overdue_for_unclaimed_case(self) -> None:
        intake_and_triage(self.service, student_id="stu-3", actor=COORDINATOR)
        self.clock.advance(hours=25)  # 超过 24 小时响应时限
        overview = coordinator_overview(self.service.repo, self.service.routing, now=self.clock())
        unclaimed = [c for c in overview["cases"] if c["student_id"] == "stu-3"]
        self.assertEqual(len(unclaimed), 1)
        self.assertTrue(unclaimed[0]["response_overdue"])
        self.assertFalse(unclaimed[0]["resolution_overdue"])


if __name__ == "__main__":
    unittest.main()
