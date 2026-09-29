"""国际学生支持案件后端。"""
from __future__ import annotations

from .errors import (
    CaseNotFoundError,
    ClaimConflictError,
    ConsentViolationError,
    DomainError,
    InvalidTransitionError,
    MergeError,
    RoutingError,
    StaffNotFoundError,
    StaffPermissionError,
)
from .models import (
    ActionRecord,
    ActionType,
    ActorRole,
    Case,
    CaseState,
    ConsentScope,
    Deadline,
    ExternalDependency,
    Group,
    IssueType,
    Material,
    MaterialCategory,
    Source,
    StaffMember,
)
from .service import CaseService

__all__ = [
    "ActionRecord",
    "ActionType",
    "ActorRole",
    "Case",
    "CaseNotFoundError",
    "CaseService",
    "CaseState",
    "ClaimConflictError",
    "ConsentScope",
    "ConsentViolationError",
    "Deadline",
    "DomainError",
    "ExternalDependency",
    "Group",
    "InvalidTransitionError",
    "IssueType",
    "Material",
    "MaterialCategory",
    "MergeError",
    "RoutingError",
    "Source",
    "StaffMember",
    "StaffNotFoundError",
    "StaffPermissionError",
]
