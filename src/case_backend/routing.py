"""分派路由：依据问题类型、机构权限与学生同意范围确定处理组。"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import ConsentError, RoutingError


@dataclass(frozen=True)
class SlaPolicy:
    """服务时限：响应时限（受理到认领）与办结时限（受理到关闭）。"""

    response_hours: int
    resolution_hours: int


@dataclass(frozen=True)
class HandlingGroup:
    """处理组及其机构权限（可处理的问题类型）。"""

    group_id: str
    name: str
    permitted_issue_types: frozenset[str]
    sla: SlaPolicy


@dataclass(frozen=True)
class RoutingTable:
    """分派配置：处理组目录 + 问题类型到候选组的优先级列表。"""

    groups: dict[str, HandlingGroup]
    routing: dict[str, tuple[str, ...]]

    def route(self, issue_type: str, consent_groups: frozenset[str]) -> HandlingGroup:
        """选出同时满足机构权限与学生同意范围的最高优先级处理组。"""
        ordered = self.routing.get(issue_type)
        if not ordered:
            raise RoutingError(f"未配置问题类型的分派路由：{issue_type}")
        for group_id in ordered:
            group = self.groups[group_id]
            if issue_type not in group.permitted_issue_types:
                raise RoutingError(f"处理组 {group_id} 无机构权限处理：{issue_type}")
            if group_id in consent_groups:
                return group
        raise ConsentError("学生同意范围内没有可承接的处理组")


def build_default_routing() -> RoutingTable:
    """支持中心的默认分派配置。"""
    groups = {
        "intern-office": HandlingGroup(
            "intern-office", "实习办公室", frozenset({"实习就业"}), SlaPolicy(24, 120)
        ),
        "academic-office": HandlingGroup(
            "academic-office", "教务办公室", frozenset({"学业课程"}), SlaPolicy(24, 120)
        ),
        "support-center": HandlingGroup(
            "support-center",
            "国际学生支持中心",
            frozenset({"实习就业", "学业课程", "签证移民", "心理健康", "其他"}),
            SlaPolicy(8, 72),
        ),
    }
    routing = {
        "实习就业": ("intern-office", "support-center"),
        "学业课程": ("academic-office", "support-center"),
        "签证移民": ("support-center",),
        "心理健康": ("support-center",),
        "其他": ("support-center",),
    }
    return RoutingTable(groups=groups, routing=routing)
