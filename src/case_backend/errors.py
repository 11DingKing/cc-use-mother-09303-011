"""领域错误类型。"""
from __future__ import annotations


class DomainError(Exception):
    """领域操作的基础错误。"""


class CaseNotFoundError(DomainError):
    """案件不存在。"""


class StaffNotFoundError(DomainError):
    """人员未登记。"""


class InvalidTransitionError(DomainError):
    """当前状态不允许该操作。"""


class ClaimConflictError(DomainError):
    """案件已被认领。"""


class RoutingError(DomainError):
    """没有符合机构权限与同意范围的处理组。"""


class ConsentViolationError(DomainError):
    """操作超出学生同意范围。"""


class StaffPermissionError(DomainError):
    """人员无权执行该操作。"""


class MergeError(DomainError):
    """重复线索合并不合法。"""
