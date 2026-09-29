"""端到端演示：一名国际学生同时向实习办公室和教务办公室求助。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from case_backend import (
    ActorRole,
    CaseService,
    ConsentScope,
    IssueType,
    Material,
    MaterialCategory,
    StaffMember,
)

START = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


def main() -> None:
    clock_t = {"t": START}
    service = CaseService(now=lambda: clock_t["t"])
    service.register_staff(
        StaffMember("coord-1", "陈协调", "支持中心", "支持中心-协调组", ActorRole.COORDINATOR)
    )
    service.register_staff(
        StaffMember("handler-intern", "李实习", "实习办公室", "实习办公室-就业组")
    )

    consent = ConsentScope(
        offices=frozenset({"实习办公室", "教务办公室", "国际事务中心"}),
        materials={
            MaterialCategory.IDENTITY: frozenset({"实习办公室", "国际事务中心"}),
            MaterialCategory.ACADEMIC_RECORD: frozenset({"教务办公室", "国际事务中心"}),
        },
    )
    materials = [
        Material("M-1", MaterialCategory.IDENTITY, "护照首页", "E1234567", "实习办公室", START),
        Material("M-2", MaterialCategory.ACADEMIC_RECORD, "在读证明", "机密", "教务办公室", START),
    ]

    # 两个办公室各自登记同一求助，产生重复线索
    first = service.intake(
        student_id="S-1001",
        issue_type=IssueType.INTERNSHIP,
        summary="实习签证与学籍状态冲突",
        source_office="实习办公室",
        consent=consent,
        materials=materials[:1],
    )
    second = service.intake(
        student_id="S-1001",
        issue_type=IssueType.INTERNSHIP,
        summary="实习签证与学籍状态冲突",
        source_office="教务办公室",
        consent=consent,
        materials=materials[1:],
    )

    # 协调员合并重复线索（来源保留），系统按规则分派，专员认领
    service.merge(first.case_id, second.case_id, actor_id="coord-1", note="同一学生同一问题")
    service.assign(first.case_id)
    service.claim(first.case_id, handler_id="handler-intern")
    service.record_action(
        first.case_id,
        actor_id="handler-intern",
        note="与国际事务中心核对签证条款",
        public_note="已与国际事务中心核对签证条款",
    )
    # 外部转介产生外部依赖；暂停后恢复；逾期前升级
    service.refer(
        first.case_id,
        actor_id="handler-intern",
        target="校外移民顾问",
        external=True,
        expected_by=START + timedelta(hours=24),
        note="等待书面意见",
    )
    service.pause(first.case_id, actor_id="coord-1", reason="等待外部机构回复")
    service.resume(first.case_id, actor_id="coord-1")
    service.escalate(first.case_id, actor_id="coord-1", reason="外部依赖临近时限")

    clock_t["t"] = START + timedelta(hours=30)
    print("== 学生统一进度 ==")
    print(json.dumps(service.student_view(first.case_id), ensure_ascii=False, indent=2))
    print("== 协调员总览（逾期与外部依赖，敏感字段已隐藏） ==")
    print(json.dumps(service.coordinator_overview(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
