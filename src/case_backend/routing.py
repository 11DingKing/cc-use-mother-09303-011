"""按问题类型、机构权限与学生同意范围分派处理组。"""
from __future__ import annotations

from .errors import RoutingError
from .models import ConsentScope, Group, IssueType

GROUPS: dict[str, Group] = {
    "实习办公室-就业组": Group(name="实习办公室-就业组", office="实习办公室"),
    "教务办公室-学籍组": Group(name="教务办公室-学籍组", office="教务办公室"),
    "教务办公室-资助组": Group(name="教务办公室-资助组", office="教务办公室"),
    "国际事务中心-签证组": Group(name="国际事务中心-签证组", office="国际事务中心"),
    "国际事务中心-综合组": Group(name="国际事务中心-综合组", office="国际事务中心"),
    "心理支持中心-关怀组": Group(name="心理支持中心-关怀组", office="心理支持中心"),
}

# 机构权限：机构被授权处理的问题类型
INSTITUTION_PERMISSIONS: dict[str, frozenset[IssueType]] = {
    "实习办公室": frozenset({IssueType.INTERNSHIP}),
    "教务办公室": frozenset({IssueType.ACADEMIC, IssueType.FINANCIAL}),
    "国际事务中心": frozenset(
        {
            IssueType.VISA,
            IssueType.ACADEMIC,
            IssueType.INTERNSHIP,
            IssueType.FINANCIAL,
            IssueType.WELLBEING,
        }
    ),
    "心理支持中心": frozenset({IssueType.WELLBEING}),
}

# 候选处理组，按优先级排序
ROUTING_TABLE: dict[IssueType, tuple[str, ...]] = {
    IssueType.INTERNSHIP: ("实习办公室-就业组", "国际事务中心-综合组"),
    IssueType.ACADEMIC: ("教务办公室-学籍组", "国际事务中心-综合组"),
    IssueType.VISA: ("国际事务中心-签证组",),
    IssueType.WELLBEING: ("心理支持中心-关怀组", "国际事务中心-综合组"),
    IssueType.FINANCIAL: ("教务办公室-资助组", "国际事务中心-综合组"),
}


def office_permits(office: str, issue_type: IssueType) -> bool:
    """机构是否被授权处理该问题类型。"""
    return issue_type in INSTITUTION_PERMISSIONS.get(office, frozenset())


def route(issue_type: IssueType, consent: ConsentScope) -> Group:
    """选出第一个同时具备机构权限与学生同意的候选处理组。"""
    for name in ROUTING_TABLE[issue_type]:
        group = GROUPS[name]
        if office_permits(group.office, issue_type) and consent.allows_office(group.office):
            return group
    raise RoutingError(f"{issue_type.value} 没有可用的处理组：机构权限或学生同意范围不足")
