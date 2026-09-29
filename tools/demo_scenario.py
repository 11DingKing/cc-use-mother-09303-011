"""端到端演示：一名国际学生同时向实习办公室和教务办公室求助。

复现并解决：两边各自启动处理、重复索取相同隐私材料、学生不知道谁负责。
运行：python3 tools/demo_scenario.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from support import (  # noqa: E402
    ACADEMIC_SPECIALIST,
    COORDINATOR,
    INTERN_SPECIALIST,
    STUDENT,
    build_service,
)

from case_backend import PendingDependencyError, coordinator_overview, student_progress  # noqa: E402


def main() -> None:
    service, clock = build_service()

    print("== 1. 学生同时向两个办公室求助，产生两条线索 ==")
    lead_a = service.intake(
        student_id="stu-1",
        issue_type="实习就业",
        source_office="实习办公室",
        description="实习单位要求出具在校证明并核实签证状态",
        consent_groups=["intern-office", "academic-office", "support-center"],
        actor=STUDENT,
    )
    lead_b = service.intake(
        student_id="stu-1",
        issue_type="学业课程",
        source_office="教务办公室",
        description="同一批材料，教务这边也要一份",
        consent_groups=["intern-office", "academic-office", "support-center"],
        actor=STUDENT,
    )
    print(f"线索A {lead_a.case_id}（实习办公室） / 线索B {lead_b.case_id}（教务办公室）")

    print("== 2. 两边各自分派、认领，并重复索取相同隐私材料 ==")
    for case_id, specialist in (
        (lead_a.case_id, INTERN_SPECIALIST),
        (lead_b.case_id, ACADEMIC_SPECIALIST),
    ):
        service.triage(case_id, actor=COORDINATOR)
        service.claim(case_id, specialist=specialist)
        service.record_action(
            case_id,
            actor=specialist,
            kind="索取材料",
            detail="请提供护照复印件",
            student_visible=True,
            material_key="护照复印件",
        )
    print("两边都索取了「护照复印件」")

    print("== 3. 协调员发现重复线索并合并，来源留痕 ==")
    duplicates = service.find_duplicate_leads("stu-1")
    print(f"发现重复线索：{[c.case_id for c in duplicates]}")
    service.merge(lead_a.case_id, lead_b.case_id, actor=COORDINATOR)

    print("== 4. 学生看到统一进度：一个案件、一个负责组、材料只索取一次 ==")
    progress = student_progress(service.repo, service.routing, "stu-1", now=clock())
    print(json.dumps(progress, ensure_ascii=False, indent=2))

    print("== 5. 主案件转介外部机构，协调员识别逾期与外部依赖 ==")
    service.refer(
        lead_a.case_id,
        actor=INTERN_SPECIALIST,
        target="企业背景核查中心",
        description="核查实习单位资质",
        expected_at=clock().replace(hour=12),
    )
    try:
        service.close(lead_a.case_id, actor=INTERN_SPECIALIST, outcome="提前办结")
    except PendingDependencyError as exc:
        print(f"关闭被拦截：{exc}")
    clock.advance(days=6)
    overview = coordinator_overview(service.repo, service.routing, now=clock())
    print(json.dumps(overview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
