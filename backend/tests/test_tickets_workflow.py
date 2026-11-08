"""N-01 工单闭环测试：状态机、权限边界、备注必填、分页筛选。

全部走内存实现（不配 mysql_url），与 MySQL 实现共用同一套规则代码路径。
运行：cd backend && python -m unittest tests.test_tickets_workflow -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services import tickets as ticket_service  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from tests.test_review_fixes import make_settings, token_for  # noqa: E402

STUDENT = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}
ADMIN = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}
OTHER = {"Authorization": f"Bearer {token_for(3, 'other', 'student')}"}


class TicketServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        ticket_service.clear_mock_tickets()
        self.settings = make_settings()

    def _create(self, user_id: int = 2, title: str = "宿舍夜归被锁门外") -> dict:
        receipt = ticket_service.create_ticket(
            self.settings, user_id, title, "宿管十一点锁门，晚自习回来进不去，希望协调。", "宿舍"
        )
        item = ticket_service.get_ticket(self.settings, 1, user_id, is_admin=False)
        assert item is not None, receipt
        return item

    def test_new_ticket_is_open(self) -> None:
        item = self._create()
        self.assertEqual(item["status"], "open")
        self.assertEqual(item["status_label"], "待受理")
        self.assertIsNone(item["handler_id"])

    def test_owner_and_admin_visibility(self) -> None:
        self._create(user_id=2)
        mine, total_mine = ticket_service.list_tickets(
            self.settings, user_id=2, is_admin=False
        )
        self.assertEqual((len(mine), total_mine), (1, 1))
        other, total_other = ticket_service.list_tickets(
            self.settings, user_id=3, is_admin=False
        )
        self.assertEqual((len(other), total_other), (0, 0), "学生不能看到别人的工单")
        everything, total_all = ticket_service.list_tickets(
            self.settings, user_id=99, is_admin=True
        )
        self.assertEqual((len(everything), total_all), (1, 1), "管理员能看到全部")

    def test_cross_user_detail_returns_none(self) -> None:
        item = self._create(user_id=2)
        self.assertIsNone(ticket_service.get_ticket(self.settings, item["id"], 3, is_admin=False))
        self.assertIsNotNone(ticket_service.get_ticket(self.settings, item["id"], 3, is_admin=True))

    def test_accept_then_process_then_close(self) -> None:
        item = self._create()
        accepted = ticket_service.transition_ticket(
            self.settings, item["id"], 1, is_admin=True, action="accept"
        )
        self.assertEqual(accepted["status"], "processing")
        self.assertEqual(accepted["handler_id"], 1)

        processed = ticket_service.transition_ticket(
            self.settings, item["id"], 1, is_admin=True, action="process", remark="已联系宿管核实"
        )
        self.assertEqual(processed["status"], "processing")
        self.assertEqual(processed["handler_remark"], "已联系宿管核实")

        closed = ticket_service.transition_ticket(
            self.settings, item["id"], 1, is_admin=True, action="close", remark="已协调宿管延长锁门至 23:30"
        )
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["status_label"], "已关闭")
        self.assertIsNotNone(closed["closed_at"])

    def test_illegal_transitions_rejected(self) -> None:
        item = self._create()
        # 未受理不能直接记录进展
        with self.assertRaises(ticket_service.TicketRuleError):
            ticket_service.transition_ticket(
                self.settings, item["id"], 1, is_admin=True, action="process", remark="跳过受理"
            )
        ticket_service.transition_ticket(self.settings, item["id"], 1, is_admin=True, action="accept")
        # 已受理不能再受理
        with self.assertRaises(ticket_service.TicketRuleError):
            ticket_service.transition_ticket(
                self.settings, item["id"], 1, is_admin=True, action="accept"
            )
        ticket_service.transition_ticket(
            self.settings, item["id"], 1, is_admin=True, action="close", remark="处理完毕，已回访"
        )
        # 终态不可再流转
        with self.assertRaises(ticket_service.TicketRuleError):
            ticket_service.transition_ticket(
                self.settings, item["id"], 1, is_admin=True, action="close", remark="再次关闭试试"
            )

    def test_close_requires_meaningful_remark(self) -> None:
        item = self._create()
        with self.assertRaises(ticket_service.TicketRuleError) as ctx:
            ticket_service.transition_ticket(
                self.settings, item["id"], 1, is_admin=True, action="close", remark="好的"
            )
        self.assertIn("至少", str(ctx.exception))

    def test_student_cannot_transition(self) -> None:
        item = self._create()
        with self.assertRaises(ticket_service.TicketPermissionError):
            ticket_service.transition_ticket(
                self.settings, item["id"], 2, is_admin=False, action="accept"
            )

    def test_keyword_and_pagination(self) -> None:
        for i in range(3):
            ticket_service.create_ticket(
                self.settings, 2, f"食堂问题{i}", f"第 {i} 次反馈食堂菜品价格与标识不符。", "食堂"
            )
        page1, total = ticket_service.list_tickets(
            self.settings, user_id=2, is_admin=False, limit=2, offset=0
        )
        page2, _ = ticket_service.list_tickets(
            self.settings, user_id=2, is_admin=False, limit=2, offset=2
        )
        self.assertEqual((len(page1), len(page2), total), (2, 1, 3))
        hit, hit_total = ticket_service.list_tickets(
            self.settings, user_id=2, is_admin=False, keyword="食堂问题1"
        )
        self.assertEqual((len(hit), hit_total), (1, 1))


class TicketApiTests(unittest.TestCase):
    def setUp(self) -> None:
        ticket_service.clear_mock_tickets()
        settings = make_settings()
        settings.auth_required = True
        settings.dev_mode = False
        services = build_services(settings)
        services.llm = FakeLLM()
        self.client = TestClient(create_app(settings, services=services))

    def _submit(self, headers: dict) -> dict:
        resp = self.client.post(
            "/api/v1/tickets",
            json={"title": "投诉宿管态度", "description": "今晚十一点被锁门外，值班室无人应答。", "category": "宿舍"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def test_auth_required(self) -> None:
        self.assertEqual(self.client.get("/api/v1/tickets").status_code, 401)

    def test_list_mine_and_detail(self) -> None:
        self._submit(STUDENT)
        mine = self.client.get("/api/v1/tickets", headers=STUDENT)
        self.assertEqual(mine.status_code, 200, mine.text)
        body = mine.json()
        self.assertEqual(body["total"], 1)
        ticket_id = body["items"][0]["id"]

        detail = self.client.get(f"/api/v1/tickets/{ticket_id}", headers=STUDENT)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["status"], "open")

        # 他人工单 → 404（不暴露存在性）
        self.assertEqual(
            self.client.get(f"/api/v1/tickets/{ticket_id}", headers=OTHER).status_code, 404
        )

    def test_transition_permissions_and_errors(self) -> None:
        ticket_id = self._submit(STUDENT)["ticket_no"]
        listed = self.client.get("/api/v1/tickets", headers=ADMIN).json()["items"][0]
        self.assertEqual(listed["ticket_no"], ticket_id)

        denied = self.client.post(
            f"/api/v1/tickets/{listed['id']}/transition",
            json={"action": "accept"},
            headers=STUDENT,
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        short = self.client.post(
            f"/api/v1/tickets/{listed['id']}/transition",
            json={"action": "close", "remark": "好了"},
            headers=ADMIN,
        )
        self.assertEqual(short.status_code, 400, short.text)

        accepted = self.client.post(
            f"/api/v1/tickets/{listed['id']}/transition",
            json={"action": "accept"},
            headers=ADMIN,
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["status"], "processing")

        closed = self.client.post(
            f"/api/v1/tickets/{listed['id']}/transition",
            json={"action": "close", "remark": "已联系宿管，门口张贴值班电话"},
            headers=ADMIN,
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertEqual(closed.json()["status"], "closed")

        # 学生能看到处理意见
        detail = self.client.get(f"/api/v1/tickets/{listed['id']}", headers=STUDENT).json()
        self.assertIn("值班电话", detail["handler_remark"] or "")

    def test_invalid_status_filter(self) -> None:
        resp = self.client.get("/api/v1/tickets?status=unknown", headers=STUDENT)
        self.assertEqual(resp.status_code, 400)
