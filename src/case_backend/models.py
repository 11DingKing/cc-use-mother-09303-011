"""案件领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Role(str, Enum):
    """契约中的三类角色。"""

    STUDENT = "国际学生"
    SPECIALIST = "支持专员"
    COORDINATOR = "项目协调员"


class CaseState(str, Enum):
    """案件状态，取值与 domain/contract.json 的 states 保持一致。"""

    INTAKE = "求助"
    ASSIGNED = "分派"
    IN_PROGRESS = "处置"
    REFERRED = "转介"
    ON_HOLD = "暂停"
    ESCALATED = "升级"
    CLOSED = "关闭"


class ActionKind(str, Enum):
    """处置行动类型。"""

    NOTE = "内部备注"
    CONTACT_STUDENT = "联系学生"
    REQUEST_MATERIAL = "索取材料"
    EXTERNAL_CONTACT = "外部沟通"


class Sensitivity(str, Enum):
    """资料敏感级别，与 student_visible 共同界定资料可见边界。"""

    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"


@dataclass(frozen=True)
class Actor:
    """操作者：角色决定权限边界，group_id 表示所属处理组。"""

    actor_id: str
    role: Role
    group_id: str | None = None


@dataclass(frozen=True)
class StateChange:
    """一次状态推进的留痕。"""

    from_state: CaseState | None
    to_state: CaseState
    actor: Actor
    at: datetime
    reason: str = ""


@dataclass(frozen=True)
class ActionRecord:
    """一次处置行动。

    student_visible 控制学生是否可见；sensitivity 标记敏感级别，
    协调员等跨组角色一律看不到 detail 内容。
    """

    action_id: str
    case_id: str
    actor: Actor
    kind: ActionKind
    detail: str
    created_at: datetime
    student_visible: bool = False
    material_key: str | None = None
    sensitivity: Sensitivity = Sensitivity.MEDIUM


@dataclass
class ExternalDependency:
    """外部依赖：转介或等待外部机构时登记，了结前不能关闭案件。"""

    dependency_id: str
    case_id: str
    target: str
    description: str
    created_at: datetime
    expected_at: datetime | None = None
    resolved_at: datetime | None = None

    @property
    def pending(self) -> bool:
        return self.resolved_at is None


@dataclass(frozen=True)
class MergedSource:
    """被合并线索的来源留痕，保证合并不丢失来源。"""

    case_id: str
    source_office: str
    intake_at: datetime
    merged_at: datetime


@dataclass
class Case:
    """支持案件聚合根。"""

    case_id: str
    student_id: str
    issue_type: str
    source_office: str
    description: str
    created_at: datetime
    state: CaseState = CaseState.INTAKE
    consent_groups: frozenset[str] = frozenset()
    group_id: str | None = None
    assignee_id: str | None = None
    response_due_at: datetime | None = None
    resolution_due_at: datetime | None = None
    closed_at: datetime | None = None
    close_outcome: str | None = None
    close_reason: str | None = None
    student_summary: str = ""
    merged_into: str | None = None
    merged_sources: list[MergedSource] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    dependencies: list[ExternalDependency] = field(default_factory=list)
    history: list[StateChange] = field(default_factory=list)
    reopen_count: int = 0
    version: int = 0

    @property
    def open(self) -> bool:
        return self.state is not CaseState.CLOSED
