"""测试共享夹具：固定时钟、确定性编号、默认服务与角色。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from case_backend import (  # noqa: E402
    Actor,
    CaseService,
    InMemoryCaseRepository,
    Role,
    build_default_routing,
)

START = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

STUDENT = Actor("stu-1", Role.STUDENT)
OTHER_STUDENT = Actor("stu-2", Role.STUDENT)
COORDINATOR = Actor("coord-1", Role.COORDINATOR)
INTERN_SPECIALIST = Actor("spec-intern", Role.SPECIALIST, group_id="intern-office")
ACADEMIC_SPECIALIST = Actor("spec-academic", Role.SPECIALIST, group_id="academic-office")
CENTER_SPECIALIST = Actor("spec-center", Role.SPECIALIST, group_id="support-center")

ALL_GROUPS = ["intern-office", "academic-office", "support-center"]


class FakeClock:
    def __init__(self, start: datetime = START) -> None:
        self.moment = start

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs) -> None:
        self.moment += timedelta(**kwargs)


def make_ids():
    sequence = count(1)
    return lambda prefix: f"{prefix}-{next(sequence)}"


def build_service(start: datetime = START) -> tuple[CaseService, FakeClock]:
    clock = FakeClock(start)
    service = CaseService(
        InMemoryCaseRepository(),
        build_default_routing(),
        clock=clock,
        id_factory=make_ids(),
    )
    return service, clock


def intake_and_triage(
    service: CaseService,
    *,
    student_id: str = "stu-1",
    issue_type: str = "实习就业",
    source_office: str = "实习办公室",
    consent_groups=ALL_GROUPS,
    actor: Actor = STUDENT,
):
    case = service.intake(
        student_id=student_id,
        issue_type=issue_type,
        source_office=source_office,
        description="需要支持",
        consent_groups=consent_groups,
        actor=actor,
    )
    return service.triage(case.case_id, actor=COORDINATOR)
