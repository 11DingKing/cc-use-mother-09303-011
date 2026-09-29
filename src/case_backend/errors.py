"""案件后端的领域异常。"""
from __future__ import annotations


class DomainError(Exception):
    """领域规则冲突的基类。"""


class ValidationError(DomainError):
    """请求参数不满足领域约束。"""


class NotFoundError(DomainError):
    """目标资源不存在。"""


class PermissionDeniedError(DomainError):
    """角色或机构权限不足。"""


class InvalidTransitionError(DomainError):
    """状态机不允许的推进。"""


class ClaimConflictError(DomainError):
    """认领冲突：案件已被认领或不在可认领状态。"""


class MergeError(DomainError):
    """重复线索合并不合法。"""


class ConsentError(DomainError):
    """超出学生同意范围。"""


class RoutingError(DomainError):
    """缺少可用的分派路由。"""


class PendingDependencyError(DomainError):
    """存在未决外部依赖，不能关闭。"""
