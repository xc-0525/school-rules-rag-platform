"""校园服务模块测试：报修限流判重、预约并发冲突、失物招领审核流。

全部走内存实现（make_settings 不配 mysql_url），不依赖 MySQL。
并发冲突在内存实现里由 threading.Lock 保证检查与插入的原子性，
与 MySQL 实现的「FOR UPDATE + UNIQUE KEY」防线一一对应。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services import lostfound as lf_service  # noqa: E402
from app.services import repairs as repair_service  # noqa: E402
from app.services import reservations as res_service  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from tests.test_review_fixes import make_settings, token_for  # noqa: E402

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
TOO_LATE = (date.today() + timedelta(days=30)).isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _repair_payload(**overrides) -> dict:
    payload = {
        "title": "卫生间水龙头漏水",
        "description": "水龙头关不严，一直在滴水，地面全是水。",
        "category": "water_power",
        "building": "12栋",
        "room": "305",
        "contact": "13800000000",
    }
    payload.update(overrides)
    return payload


class RepairServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        repair_service.clear_mock_repairs()
        self.settings = make_settings()

    def test_create_and_owner_visibility(self) -> None:
        created = repair_service.create_repair(self.settings, 1, _repair_payload())
        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["category_label"], "水电")

        mine = repair_service.list_repairs(self.settings, user_id=1, is_admin=False)
        self.assertEqual([r["id"] for r in mine], [created["id"]])
        other = repair_service.list_repairs(self.settings, user_id=2, is_admin=False)
        self.assertEqual(other, [], "学生不能看到别人的报修")
        everything = repair_service.list_repairs(self.settings, user_id=99, is_admin=True)
        self.assertEqual(len(everything), 1, "管理员能看到全部")

    def test_daily_rate_limit(self) -> None:
        for i in range(3):
            repair_service.create_repair(
                self.settings, 1, _repair_payload(room=f"{300 + i}")
            )
        with self.assertRaises(repair_service.RepairRuleError) as ctx:
            repair_service.create_repair(self.settings, 1, _repair_payload(room="399"))
        self.assertIn("每日最多", str(ctx.exception))

    def test_duplicate_detection(self) -> None:
        repair_service.create_repair(self.settings, 1, _repair_payload())
        # 同用户同房间同类别 → 重复（即使换个标题）
        with self.assertRaises(repair_service.RepairRuleError) as ctx:
            repair_service.create_repair(
                self.settings, 1, _repair_payload(title="再次反馈漏水")
            )
        self.assertIn("重复提交", str(ctx.exception))
        # 不同房间不受影响
        ok = repair_service.create_repair(self.settings, 1, _repair_payload(room="306"))
        self.assertTrue(ok["id"])

    def test_input_validation(self) -> None:
        for bad in (
            _repair_payload(title="  "),
            _repair_payload(description="太短"),
            _repair_payload(category="magic"),
            _repair_payload(building=""),
            _repair_payload(contact=""),
        ):
            with self.assertRaises(repair_service.RepairRuleError, msg=bad["title"]):
                repair_service.create_repair(self.settings, 1, bad)

    def test_transition_state_machine(self) -> None:
        created = repair_service.create_repair(self.settings, 1, _repair_payload())
        rid = created["id"]

        # 学生不能受理
        with self.assertRaises(repair_service.RepairRuleError):
            repair_service.transition_repair(
                self.settings, rid, 1, is_admin=False, action="process"
            )
        # 管理员受理 → 完成
        processing = repair_service.transition_repair(
            self.settings, rid, 9, is_admin=True, action="process", remark="已联系师傅"
        )
        self.assertEqual(processing["status"], "processing")
        self.assertEqual(processing["handler_id"], 9)
        completed = repair_service.transition_repair(
            self.settings, rid, 9, is_admin=True, action="complete"
        )
        self.assertEqual(completed["status"], "completed")
        # 终态不能再流转
        with self.assertRaises(repair_service.RepairRuleError):
            repair_service.transition_repair(
                self.settings, rid, 9, is_admin=True, action="close", remark="x"
            )

    def test_cancel_rules(self) -> None:
        a = repair_service.create_repair(self.settings, 1, _repair_payload(room="401"))
        b = repair_service.create_repair(self.settings, 1, _repair_payload(room="402"))

        # 学生 A 不能取消 B 的单
        self.assertIsNone(
            repair_service.transition_repair(
                self.settings, b["id"], 2, is_admin=False, action="cancel"
            )
        )
        cancelled = repair_service.transition_repair(
            self.settings, a["id"], 1, is_admin=False, action="cancel"
        )
        self.assertEqual(cancelled["status"], "closed")
        # 已受理的单不能被学生取消
        repair_service.transition_repair(
            self.settings, b["id"], 9, is_admin=True, action="process"
        )
        with self.assertRaises(repair_service.RepairRuleError):
            repair_service.transition_repair(
                self.settings, b["id"], 1, is_admin=False, action="cancel"
            )

    def test_close_requires_reason(self) -> None:
        created = repair_service.create_repair(self.settings, 1, _repair_payload())
        with self.assertRaises(repair_service.RepairRuleError):
            repair_service.transition_repair(
                self.settings, created["id"], 9, is_admin=True, action="close", remark="  "
            )


class ReservationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        res_service.clear_mock_reservations()
        self.settings = make_settings()

    def test_date_range_rules(self) -> None:
        base = dict(location_id=1, slot="morning")
        for bad_date, msg in ((YESTERDAY, "过去"), (date.today().isoformat(), "今天"), (TOO_LATE, "太远")):
            with self.assertRaises(res_service.ReservationRuleError, msg=msg) as ctx:
                res_service.create_reservation(self.settings, 1, res_date=bad_date, **base)
            self.assertIn("预约", str(ctx.exception))
        # 非法输入
        with self.assertRaises(res_service.ReservationRuleError):
            res_service.create_reservation(self.settings, 1, res_date="2026/01/01", **base)
        with self.assertRaises(res_service.ReservationRuleError):
            res_service.create_reservation(
                self.settings, 1, location_id=1, res_date=TOMORROW, slot="noon"
            )

    def test_create_and_availability(self) -> None:
        created = res_service.create_reservation(
            self.settings, 1, location_id=2, res_date=TOMORROW, slot="afternoon"
        )
        self.assertEqual(created["status"], "reserved")
        self.assertEqual(created["location_name"], "图书馆二楼研讨间 201")
        self.assertEqual(created["slot_label"], "下午 12:00-17:00")

        board = res_service.availability(
            self.settings, location_id=2, res_date=TOMORROW
        )
        booked = {s["slot"]: s["booked"] for s in board["slots"]}
        self.assertTrue(booked["afternoon"])
        self.assertFalse(booked["morning"])

    def test_conflict_then_cancel_then_retry(self) -> None:
        # 研讨间按整间预约（容量 1），第二人必然冲突
        res_service.create_reservation(
            self.settings, 1, location_id=2, res_date=TOMORROW, slot="morning"
        )
        with self.assertRaises(res_service.SlotConflictError):
            res_service.create_reservation(
                self.settings, 2, location_id=2, res_date=TOMORROW, slot="morning"
            )
        # 取消后他人可以约同一时段
        mine = res_service.list_reservations(self.settings, user_id=1, is_admin=False)
        res_service.transition_reservation(
            self.settings, mine[0]["id"], 1, is_admin=False, action="cancel"
        )
        ok = res_service.create_reservation(
            self.settings, 2, location_id=2, res_date=TOMORROW, slot="morning"
        )
        self.assertEqual(ok["user_id"], 2)

    def test_capacity_allows_multiple_until_full(self) -> None:
        """自习区容量 40：多人可约同一时段，余量随之递减。"""
        for user_id in (1, 2, 3):
            res_service.create_reservation(
                self.settings, user_id, location_id=1, res_date=TOMORROW, slot="morning"
            )
        board = res_service.availability(self.settings, location_id=1, res_date=TOMORROW)
        morning = next(s for s in board["slots"] if s["slot"] == "morning")
        self.assertEqual(morning["capacity"], 40)
        self.assertEqual(morning["booked_count"], 3)
        self.assertEqual(morning["remaining"], 37)
        self.assertFalse(morning["booked"], "未满时不应标记为已约满")

    def test_same_user_cannot_book_same_slot_twice(self) -> None:
        res_service.create_reservation(
            self.settings, 1, location_id=1, res_date=TOMORROW, slot="morning"
        )
        with self.assertRaises(res_service.SlotConflictError) as ctx:
            res_service.create_reservation(
                self.settings, 1, location_id=1, res_date=TOMORROW, slot="morning"
            )
        self.assertIn("已经预约过", str(ctx.exception))

    def test_active_quota(self) -> None:
        slots = ["morning", "afternoon", "evening"]
        dates = [TOMORROW, (date.today() + timedelta(days=2)).isoformat(), (date.today() + timedelta(days=3)).isoformat()]
        for slot, day in zip(slots, dates):
            res_service.create_reservation(self.settings, 1, location_id=1, res_date=day, slot=slot)
        with self.assertRaises(res_service.ReservationRuleError) as ctx:
            res_service.create_reservation(
                self.settings, 1, location_id=1, res_date=(date.today() + timedelta(days=4)).isoformat(), slot="morning"
            )
        self.assertIn("最多同时保留", str(ctx.exception))

    def test_concurrent_booking_single_winner(self) -> None:
        """8 个线程同时抢同一时段：恰好一个成功，其余 SlotConflictError。"""
        settings = make_settings()
        res_service.seed_mock_locations()

        def attempt(user_id: int):
            try:
                res_service.create_reservation(
                    settings, user_id, location_id=3, res_date=TOMORROW, slot="evening"
                )
                return True
            except res_service.SlotConflictError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(1, 9)))
        self.assertEqual(sum(results), 1, f"应只有一个成功，实际 {sum(results)}")

    def test_transition_permissions(self) -> None:
        created = res_service.create_reservation(
            self.settings, 1, location_id=4, res_date=TOMORROW, slot="morning"
        )
        # 学生不能标记 no_show / fulfill
        with self.assertRaises(res_service.ReservationRuleError):
            res_service.transition_reservation(
                self.settings, created["id"], 1, is_admin=False, action="no_show"
            )
        # 管理员标记违约
        no_show = res_service.transition_reservation(
            self.settings, created["id"], 9, is_admin=True, action="no_show"
        )
        self.assertEqual(no_show["status"], "no_show")
        # 终态不能再取消
        with self.assertRaises(res_service.ReservationRuleError):
            res_service.transition_reservation(
                self.settings, created["id"], 9, is_admin=True, action="cancel"
            )


def _lf_payload(**overrides) -> dict:
    payload = {
        "kind": "found",
        "title": "捡到一张校园卡",
        "description": "在图书馆三楼捡到校园卡一张，姓名已打码，请失主带着证明来认领。",
        "location": "图书馆三楼",
        "contact": "QQ 123456",
    }
    payload.update(overrides)
    return payload


class LostFoundServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        lf_service.clear_mock_lostfound()
        self.settings = make_settings()

    def test_submit_goes_to_review_hidden_from_public(self) -> None:
        created = lf_service.create_item(self.settings, 1, _lf_payload())
        self.assertEqual(created["status"], "pending_review")
        self.assertEqual(created["kind_label"], "招领")
        public = lf_service.list_items(self.settings, user_id=2, is_admin=False)
        self.assertEqual(public, [], "未审核条目不能出现在公列表")
        mine = lf_service.list_items(self.settings, user_id=1, is_admin=False, scope="mine")
        self.assertEqual(len(mine), 1)

    def test_review_flow(self) -> None:
        item = lf_service.create_item(self.settings, 1, _lf_payload())
        # 驳回必须带理由
        with self.assertRaises(lf_service.LostFoundRuleError):
            lf_service.review_item(self.settings, item["id"], 9, action="reject")
        rejected = lf_service.review_item(
            self.settings, item["id"], 9, action="reject", remark="照片模糊，请补图后重发"
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertIn("补图", rejected["review_remark"])
        # rejected 是终态，不能直接通过
        with self.assertRaises(lf_service.LostFoundRuleError):
            lf_service.review_item(self.settings, item["id"], 9, action="approve")

        # 另一条：通过 → 公开 → 发布人确认解决
        other = lf_service.create_item(self.settings, 2, _lf_payload(title="捡到一把钥匙"))
        approved = lf_service.review_item(self.settings, other["id"], 9, action="approve")
        self.assertEqual(approved["status"], "published")
        public = lf_service.list_items(self.settings, user_id=1, is_admin=False)
        self.assertEqual([i["id"] for i in public], [other["id"]])
        resolved = lf_service.resolve_item(self.settings, other["id"], 2, is_admin=False)
        self.assertEqual(resolved["status"], "resolved")

    def test_image_url_sanitization(self) -> None:
        with self.assertRaises(lf_service.LostFoundRuleError):
            lf_service.create_item(
                self.settings, 1, _lf_payload(image_urls=["https://evil.example.com/x.png"])
            )
        with self.assertRaises(lf_service.LostFoundRuleError):
            lf_service.create_item(
                self.settings, 1, _lf_payload(image_urls=["/api/v1/lost-found/images/../../etc/passwd"])
            )
        ok = lf_service.create_item(
            self.settings,
            1,
            _lf_payload(
                image_urls=[
                    "/api/v1/lost-found/images/abcd.png",
                    "/api/v1/lost-found/images/abcd.png",  # 重复去重
                ]
            ),
        )
        self.assertEqual(ok["images"], ["/api/v1/lost-found/images/abcd.png"])

    def test_resolve_only_from_published(self) -> None:
        item = lf_service.create_item(self.settings, 1, _lf_payload())
        with self.assertRaises(lf_service.LostFoundRuleError):
            lf_service.resolve_item(self.settings, item["id"], 1, is_admin=False)


class CampusApiTests(unittest.TestCase):
    """接口层：错误码映射与权限边界（服务层规则已在上面覆盖）。"""

    def setUp(self) -> None:
        repair_service.clear_mock_repairs()
        res_service.clear_mock_reservations()
        lf_service.clear_mock_lostfound()

    def _client(self):
        settings = make_settings()
        settings.auth_required = True
        settings.dev_mode = False
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services)), settings

    def test_repairs_api_mapping(self) -> None:
        client, _ = self._client()
        student = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}
        admin = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}

        self.assertEqual(client.get("/api/v1/repairs").status_code, 401)

        created = client.post("/api/v1/repairs", json=_repair_payload(), headers=student)
        self.assertEqual(created.status_code, 201, created.text)

        # 重复 → 409
        dup = client.post("/api/v1/repairs", json=_repair_payload(title="再来一单"), headers=student)
        self.assertEqual(dup.status_code, 409)

        # 学生不能受理 → 400（业务规则）；管理员可以
        deny = client.post(
            f"/api/v1/repairs/{created.json()['id']}/transition",
            json={"action": "process"},
            headers=student,
        )
        self.assertEqual(deny.status_code, 400)
        accept = client.post(
            f"/api/v1/repairs/{created.json()['id']}/transition",
            json={"action": "process", "remark": "已派单"},
            headers=admin,
        )
        self.assertEqual(accept.status_code, 200)
        self.assertEqual(accept.json()["status"], "processing")

    def test_reservations_api_mapping(self) -> None:
        client, _ = self._client()
        a = {"Authorization": f"Bearer {token_for(2, 'a', 'student')}"}
        b = {"Authorization": f"Bearer {token_for(3, 'b', 'student')}"}

        locations = client.get("/api/v1/reservations/locations", headers=a)
        self.assertEqual(locations.status_code, 200, locations.text)
        self.assertGreaterEqual(len(locations.json()), 1, "种子位置可用")
        # 容量 1 的研讨间（整间预约）才能用“第二人冲突”验证 409
        room = next(loc for loc in locations.json() if loc["capacity"] == 1)

        first = client.post(
            "/api/v1/reservations",
            json={"location_id": room["id"], "res_date": TOMORROW, "slot": "morning"},
            headers=a,
        )
        self.assertEqual(first.status_code, 201, first.text)
        conflict = client.post(
            "/api/v1/reservations",
            json={"location_id": room["id"], "res_date": TOMORROW, "slot": "morning"},
            headers=b,
        )
        self.assertEqual(conflict.status_code, 409)

        past = client.post(
            "/api/v1/reservations",
            json={"location_id": locations.json()[0]["id"], "res_date": YESTERDAY, "slot": "morning"},
            headers=a,
        )
        self.assertEqual(past.status_code, 400)

        cancel = client.post(
            f"/api/v1/reservations/{first.json()['id']}/cancel", headers=a
        )
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()["status"], "cancelled")

    def test_lostfound_api_permissions(self) -> None:
        client, _ = self._client()
        student = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}
        admin = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}

        created = client.post("/api/v1/lost-found", json=_lf_payload(), headers=student)
        self.assertEqual(created.status_code, 201, created.text)
        item_id = created.json()["id"]

        # 学生不能看待审列表、不能审批
        self.assertEqual(
            client.get("/api/v1/lost-found?scope=pending", headers=student).status_code, 403
        )
        self.assertEqual(
            client.post(
                f"/api/v1/lost-found/{item_id}/review",
                json={"action": "approve"},
                headers=student,
            ).status_code,
            403,
        )
        # 管理员驳回 → 理由回到学生"我的发布"里
        rejected = client.post(
            f"/api/v1/lost-found/{item_id}/review",
            json={"action": "reject", "remark": "缺少关键信息"},
            headers=admin,
        )
        self.assertEqual(rejected.status_code, 200)
        mine = client.get("/api/v1/lost-found?scope=mine", headers=student)
        self.assertEqual(mine.json()[0]["status"], "rejected")
        self.assertEqual(mine.json()[0]["review_remark"], "缺少关键信息")


if __name__ == "__main__":
    unittest.main(verbosity=2)
