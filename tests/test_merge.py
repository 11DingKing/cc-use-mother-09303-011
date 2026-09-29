"""重复线索合并：可以合并但不能丢失来源。"""
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

from case_backend import CaseState, InvalidTransitionError, MergeError


def _two_office_leads(service):
    """复现场景：同一学生同时向实习办公室和教务办公室求助。"""
    lead_a = intake_and_triage(
        service, issue_type="实习就业", source_office="实习办公室"
    )
    lead_b = intake_and_triage(
        service, issue_type="学业课程", source_office="教务办公室"
    )
    return lead_a, lead_b


class MergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = build_service()
        self.lead_a, self.lead_b = _two_office_leads(self.service)

    def test_merge_keeps_source_trace(self) -> None:
        self.service.claim(self.lead_b.case_id, specialist=ACADEMIC_SPECIALIST)
        self.service.record_action(
            self.lead_b.case_id,
            actor=ACADEMIC_SPECIALIST,
            kind="索取材料",
            detail="请提供护照复印件",
            student_visible=True,
            material_key="护照复印件",
        )
        primary = self.service.merge(
            self.lead_a.case_id, self.lead_b.case_id, actor=COORDINATOR
        )

        duplicate = self.service.repo.get(self.lead_b.case_id)
        self.assertEqual(duplicate.state, CaseState.CLOSED)
        self.assertEqual(duplicate.close_reason, "merged")
        self.assertEqual(duplicate.merged_into, primary.case_id)
        # 来源不丢失：被合并线索及其行动流水仍可完整查询
        self.assertEqual(len(duplicate.actions), 1)
        self.assertEqual(duplicate.source_office, "教务办公室")

        (source,) = primary.merged_sources
        self.assertEqual(source.case_id, self.lead_b.case_id)
        self.assertEqual(source.source_office, "教务办公室")

    def test_merge_rejects_invalid_pairs(self) -> None:
        with self.assertRaises(MergeError):
            self.service.merge(self.lead_a.case_id, self.lead_a.case_id, actor=COORDINATOR)

        other = intake_and_triage(self.service, student_id="stu-2", actor=COORDINATOR)
        with self.assertRaises(MergeError):
            self.service.merge(self.lead_a.case_id, other.case_id, actor=COORDINATOR)

    def test_double_merge_rejected(self) -> None:
        self.service.merge(self.lead_a.case_id, self.lead_b.case_id, actor=COORDINATOR)
        lead_c = intake_and_triage(self.service, source_office="国际学生支持中心")
        with self.assertRaises(MergeError):
            self.service.merge(lead_c.case_id, self.lead_b.case_id, actor=COORDINATOR)

    def test_merged_lead_cannot_reopen(self) -> None:
        self.service.merge(self.lead_a.case_id, self.lead_b.case_id, actor=COORDINATOR)
        with self.assertRaises(InvalidTransitionError):
            self.service.reopen(self.lead_b.case_id, actor=COORDINATOR, reason="误合并")

    def test_find_duplicate_leads(self) -> None:
        duplicates = self.service.find_duplicate_leads("stu-1")
        self.assertEqual({c.case_id for c in duplicates}, {self.lead_a.case_id, self.lead_b.case_id})
        self.service.merge(self.lead_a.case_id, self.lead_b.case_id, actor=COORDINATOR)
        self.assertEqual(self.service.find_duplicate_leads("stu-1"), [])


if __name__ == "__main__":
    unittest.main()
