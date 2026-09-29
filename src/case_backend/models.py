"""案件领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class CaseState(str, Enum):
    """案件状态。"""

    INTAKE = "求助"
    ASSIGNED = "分派"
    IN_PROGRESS = "处置"
    REFERRED = "转介"
    PAUSED = "暂停"
    CLOSED = "关闭"


class IssueType(str, Enum):
    """问题类型。"""

    INTERNSHIP = "实习就业"
    ACADEMIC = "学籍学业"
    VISA = "签证居留"
    WELLBEING = "心理健康"
    FINANCIAL = "财务资助"


class MaterialCategory(str, Enum):
    """隐私材料类别。"""

    IDENTITY = "身份信息"
    ACADEMIC_RECORD = "学籍记录"
    HEALTH = "医疗健康"
    FINANCE = "财务信息"
    CONTACT = "联系方式"


class ActorRole(str, Enum):
    """角色。"""

    STUDENT = "国际学生"
    HANDLER = "支持专员"
    COORDINATOR = "项目协调员"


class ActionType(str, Enum):
    """行动类型。"""

    INTAKE = "登记"
    MERGE = "合并"
    ASSIGN = "分派"
    CLAIM = "认领"
    NOTE = "记录"
    REFER = "转介"
    PAUSE = "暂停"
    RESUME = "恢复"
    ESCALATE = "升级"
    REOPEN = "重开"
    CLOSE = "关闭"


@dataclass(frozen=True)
class ConsentScope:
    """学生同意范围：可受理机构，以及各类材料对哪些机构可见。"""

    offices: frozenset[str]
    materials: dict[MaterialCategory, frozenset[str]]

    def allows_office(self, office: str) -> bool:
        return office in self.offices

    def allows_material(self, category: MaterialCategory, office: str) -> bool:
        return office in self.materials.get(category, frozenset())

    def intersect(self, other: ConsentScope) -> ConsentScope:
        """合并线索时取交集，保持最小授权。"""
        offices = self.offices & other.offices
        categories = set(self.materials) | set(other.materials)
        materials = {
            category: self.materials.get(category, frozenset())
            & other.materials.get(category, frozenset())
            for category in categories
        }
        return ConsentScope(offices=offices, materials=materials)


@dataclass(frozen=True)
class Group:
    """处理组及其所属机构。"""

    name: str
    office: str


@dataclass(frozen=True)
class StaffMember:
    """登记人员。"""

    staff_id: str
    name: str
    office: str
    group: str
    role: ActorRole = ActorRole.HANDLER


@dataclass(frozen=True)
class Source:
    """线索来源，合并后仍需保留。"""

    office: str
    channel: str
    received_at: datetime
    note: str = ""


@dataclass(frozen=True)
class Material:
    """学生提供的隐私材料。"""

    material_id: str
    category: MaterialCategory
    title: str
    content: str
    source_office: str
    uploaded_at: datetime


@dataclass
class Deadline:
    """服务时限。"""

    kind: str
    due_at: datetime
    met_at: datetime | None = None

    def is_overdue(self, now: datetime) -> bool:
        return self.met_at is None and now > self.due_at


@dataclass
class ExternalDependency:
    """外部机构依赖。"""

    target: str
    created_at: datetime
    expected_by: datetime
    fulfilled_at: datetime | None = None

    def is_pending(self) -> bool:
        return self.fulfilled_at is None

    def is_overdue(self, now: datetime) -> bool:
        return self.is_pending() and now > self.expected_by


@dataclass
class ActionRecord:
    """行动记录；public_note 存在时对学生可见。"""

    seq: int
    at: datetime
    actor: str
    role: ActorRole
    office: str | None
    action: ActionType
    detail: str
    public_note: str | None = None


@dataclass
class Case:
    """支持案件。"""

    case_id: str
    student_id: str
    issue_type: IssueType
    summary: str
    consent: ConsentScope
    created_at: datetime
    state: CaseState = CaseState.INTAKE
    sources: list[Source] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    assigned_group: str | None = None
    assigned_office: str | None = None
    claimed_by: str | None = None
    referral_target: str | None = None
    escalation_level: int = 0
    paused_from: CaseState | None = None
    pause_reason: str | None = None
    deadlines: list[Deadline] = field(default_factory=list)
    dependencies: list[ExternalDependency] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    merged_case_ids: list[str] = field(default_factory=list)
    merged_into: str | None = None
    outcome: str | None = None
    updated_at: datetime | None = None
