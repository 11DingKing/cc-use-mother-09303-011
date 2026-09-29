"""基于标准库 http.server 的案件后端 JSON 接口。

运行：python3 -m case_backend.api  [host] [port]
身份：请求头 X-Actor-Id、X-Actor-Role（student/specialist/coordinator）、X-Group-Id（专员必填）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import (
    ClaimConflictError,
    DomainError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
)
from .models import Actor, Role
from .repository import InMemoryCaseRepository
from .routing import build_default_routing
from .services import CaseService
from .views import case_to_dict, coordinator_overview, student_progress

ROLE_TOKENS = {
    "student": Role.STUDENT,
    "specialist": Role.SPECIALIST,
    "coordinator": Role.COORDINATOR,
}


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Api:
    """把 HTTP 请求映射到 CaseService；与传输层解耦，便于直接测试。"""

    def __init__(self, service: CaseService) -> None:
        self._service = service

    def handle(self, method: str, path: str, *, actor: Actor, body: dict | None = None) -> tuple[int, dict]:
        body = body or {}
        parts = [p for p in path.strip("/").split("/") if p]
        try:
            return self._route(method, parts, actor, body)
        except NotFoundError as exc:
            return 404, {"error": str(exc)}
        except PermissionDeniedError as exc:
            return 403, {"error": str(exc)}
        except (ClaimConflictError, InvalidTransitionError) as exc:
            return 409, {"error": str(exc)}
        except KeyError as exc:
            return 400, {"error": f"缺少字段：{exc}"}
        except DomainError as exc:
            return 422, {"error": str(exc)}

    def _route(self, method: str, parts: list[str], actor: Actor, body: dict) -> tuple[int, dict]:
        svc = self._service
        if method == "POST" and parts == ["leads"]:
            case = svc.intake(
                student_id=body["student_id"],
                issue_type=body["issue_type"],
                source_office=body["source_office"],
                description=body.get("description", ""),
                consent_groups=body.get("consent_groups", []),
                actor=actor,
            )
            return 201, case_to_dict(case)
        if method == "POST" and parts == ["cases", "merge"]:
            return 200, case_to_dict(svc.merge(body["primary_id"], body["duplicate_id"], actor=actor))
        if method == "GET" and len(parts) == 3 and parts[0] == "students" and parts[2] == "progress":
            if actor.role is Role.STUDENT and actor.actor_id != parts[1]:
                raise PermissionDeniedError("学生只能查看本人进度")
            return 200, student_progress(svc.repo, svc.routing, parts[1], now=svc.now())
        if method == "GET" and parts == ["coordinator", "overview"]:
            if actor.role is not Role.COORDINATOR:
                raise PermissionDeniedError("只有项目协调员可以查看调度概览")
            return 200, coordinator_overview(svc.repo, svc.routing, now=svc.now())
        if method == "POST" and len(parts) == 3 and parts[0] == "cases":
            case_id, action = parts[1], parts[2]
            return 200, case_to_dict(self._case_action(case_id, action, actor, body))
        if (
            method == "POST"
            and len(parts) == 5
            and parts[0] == "cases"
            and parts[2] == "dependencies"
            and parts[4] == "resolve"
        ):
            case = svc.resolve_dependency(parts[1], parts[3], actor=actor)
            return 200, case_to_dict(case)
        return 404, {"error": "路径不存在"}

    def _case_action(self, case_id: str, action: str, actor: Actor, body: dict):
        svc = self._service
        if action == "triage":
            return svc.triage(case_id, actor=actor)
        if action == "claim":
            return svc.claim(case_id, specialist=actor)
        if action == "actions":
            return svc.record_action(
                case_id,
                actor=actor,
                kind=body["kind"],
                detail=body.get("detail", ""),
                student_visible=bool(body.get("student_visible", False)),
                material_key=body.get("material_key"),
            )
        if action == "refer":
            return svc.refer(
                case_id,
                actor=actor,
                target=body["target"],
                description=body.get("description", ""),
                expected_at=_parse_dt(body.get("expected_at")),
            )
        if action == "pause":
            return svc.pause(case_id, actor=actor, reason=body.get("reason", ""))
        if action == "resume":
            return svc.resume(case_id, actor=actor)
        if action == "escalate":
            return svc.escalate(case_id, actor=actor, reason=body.get("reason", ""))
        if action == "deescalate":
            return svc.deescalate(case_id, actor=actor)
        if action == "close":
            return svc.close(
                case_id,
                actor=actor,
                outcome=body.get("outcome", ""),
                student_summary=body.get("student_summary", ""),
            )
        if action == "reopen":
            return svc.reopen(case_id, actor=actor, reason=body.get("reason", ""))
        if action == "consent":
            return svc.grant_consent(case_id, body["group_id"], actor=actor)
        raise NotFoundError(f"未知的案件操作：{action}")


class _Handler(BaseHTTPRequestHandler):
    api: Api  # 由 create_server 绑定

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:
        role = ROLE_TOKENS.get(self.headers.get("X-Actor-Role", ""))
        if role is None:
            self._respond(401, {"error": "缺少或非法的 X-Actor-Role"})
            return
        actor = Actor(
            actor_id=self.headers.get("X-Actor-Id", "anonymous"),
            role=role,
            group_id=self.headers.get("X-Group-Id") or None,
        )
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "请求体不是合法 JSON"})
            return
        status, payload = self.api.handle(
            self.command, self.path.split("?", 1)[0], actor=actor, body=body
        )
        self._respond(status, payload)

    def _respond(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def build_service() -> CaseService:
    """按默认路由配置构建服务实例。"""
    return CaseService(InMemoryCaseRepository(), build_default_routing())


def create_server(api: Api, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"api": api})
    return ThreadingHTTPServer((host, port), handler)


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    server = create_server(Api(build_service()), host, port)
    print(f"案件后端已启动：http://{host}:{port}")
    server.serve_forever()
