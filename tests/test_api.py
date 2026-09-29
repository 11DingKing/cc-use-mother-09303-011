"""HTTP 接口：真实套接字冒烟 + 状态码映射。"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from support import build_service

from case_backend.api import Api, create_server

STUDENT_HEADERS = {"X-Actor-Id": "stu-1", "X-Actor-Role": "student"}
COORD_HEADERS = {"X-Actor-Id": "coord-1", "X-Actor-Role": "coordinator"}
SPECIALIST_HEADERS = {
    "X-Actor-Id": "spec-1",
    "X-Actor-Role": "specialist",
    "X-Group-Id": "intern-office",
}


class ApiHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service, _ = build_service()
        cls.server = create_server(Api(cls.service), port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def call(self, method, path, body=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_full_flow_over_http(self) -> None:
        status, lead = self.call(
            "POST",
            "/leads",
            {
                "student_id": "stu-1",
                "issue_type": "实习就业",
                "source_office": "实习办公室",
                "description": "实习签证咨询",
                "consent_groups": ["intern-office"],
            },
            STUDENT_HEADERS,
        )
        self.assertEqual(status, 201)
        self.assertEqual(lead["state"], "求助")

        case_id = lead["case_id"]
        status, body = self.call("POST", f"/cases/{case_id}/triage", {}, COORD_HEADERS)
        self.assertEqual((status, body["state"], body["group_id"]), (200, "分派", "intern-office"))

        status, body = self.call("POST", f"/cases/{case_id}/claim", {}, SPECIALIST_HEADERS)
        self.assertEqual((status, body["assignee_id"]), (200, "spec-1"))

        # 认领只能成功一次：第二次认领返回 409
        status, body = self.call("POST", f"/cases/{case_id}/claim", {}, SPECIALIST_HEADERS)
        self.assertEqual(status, 409)

        status, progress = self.call("GET", "/students/stu-1/progress", headers=STUDENT_HEADERS)
        self.assertEqual(status, 200)
        self.assertEqual(progress["cases"][0]["responsible_group"], "实习办公室")

        status, overview = self.call("GET", "/coordinator/overview", headers=COORD_HEADERS)
        self.assertEqual(status, 200)
        self.assertIn(case_id, {c["case_id"] for c in overview["cases"]})

    def test_error_status_mapping(self) -> None:
        status, _ = self.call("POST", "/cases/case-404/triage", {}, COORD_HEADERS)
        self.assertEqual(status, 404)

        status, _ = self.call("GET", "/coordinator/overview", headers=STUDENT_HEADERS)
        self.assertEqual(status, 403)

        status, _ = self.call("GET", "/students/stu-1/progress", headers=None)
        self.assertEqual(status, 401)

        status, _ = self.call("POST", "/leads", {"student_id": "stu-1"}, STUDENT_HEADERS)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
