"""列表筛选与分页（M-11/M-12/M-13）。

只验证"新增的可选参数生效"且"返回结构没变"——这三项是修改类改动，
接口契约必须保持为 list，不能改成 {items,total} 破坏既有调用。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services import lostfound as lostfound_service  # noqa: E402
from app.services import repairs as repair_service  # noqa: E402
from app.services import reservations as reservation_service  # noqa: E402


def make_settings() -> Settings:
    return Settings(llm_mode="fake", mysql_url="")


def clear_all() -> None:
    repair_service.clear_mock_repairs()
    lostfound_service.clear_mock_lostfound()
    reservation_service.clear_mock_reservations()


class RepairFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_all()
        self.settings = make_settings()

    def _create(self, title: str, building: str, room: str, description: str = "测试故障描述") -> int:
        row = repair_service.create_repair(
            self.settings,
            1,
            {
                "title": title,
                "description": description,
                "category": "water_power",
                "building": building,
                "room": room,
                "contact": "13800000000",
            },
        )
        return int(row["id"])

    def test_returns_list_contract(self) -> None:
        self._create("水龙头漏水", "12栋", "305")
        rows = repair_service.list_repairs(
            self.settings, user_id=1, is_admin=True
        )
        self.assertIsInstance(rows, list)

    def test_keyword_filter(self) -> None:
        self._create("水龙头漏水", "12栋", "305")
        self._create("空调不制冷", "12栋", "306")
        hit = repair_service.list_repairs(
            self.settings, user_id=1, is_admin=True, keyword="空调"
        )
        self.assertEqual(len(hit), 1)
        self.assertIn("空调", hit[0]["title"])
        miss = repair_service.list_repairs(
            self.settings, user_id=1, is_admin=True, keyword="不存在的关键词"
        )
        self.assertEqual(miss, [])

    def test_building_filter(self) -> None:
        self._create("水龙头漏水", "12栋", "305")
        self._create("门锁坏了", "3栋", "201")
        hit = repair_service.list_repairs(
            self.settings, user_id=1, is_admin=True, building="12栋"
        )
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["building"], "12栋")

    def test_offset_pagination(self) -> None:
        for index in range(3):
            self._create(f"分页测试 {index}", "9栋", f"10{index}")
        first = repair_service.list_repairs(
            self.settings, user_id=1, is_admin=True, limit=2, offset=0
        )
        second = repair_service.list_repairs(
            self.settings, user_id=1, is_admin=True, limit=2, offset=2
        )
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 1)
        self.assertFalse({row["id"] for row in first} & {row["id"] for row in second})


class ReservationFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_all()
        self.settings = make_settings()
        reservation_service.seed_mock_locations()
        self.today = date.today()

    def _create(self, days_ahead: int) -> None:
        target = (self.today + timedelta(days=days_ahead)).isoformat()
        reservation_service.create_reservation(
            self.settings,
            1,
            location_id=1,
            res_date=target,
            slot="morning",
        )

    def test_returns_list_contract(self) -> None:
        self._create(2)
        rows = reservation_service.list_reservations(
            self.settings, user_id=1, is_admin=False
        )
        self.assertIsInstance(rows, list)

    def test_date_range_filter(self) -> None:
        self._create(2)
        self._create(5)
        narrow_from = (self.today + timedelta(days=4)).isoformat()
        rows = reservation_service.list_reservations(
            self.settings, user_id=1, is_admin=False, date_from=narrow_from
        )
        self.assertTrue(rows)
        for row in rows:
            self.assertGreaterEqual(str(row["res_date"]), narrow_from)

        upper = (self.today + timedelta(days=3)).isoformat()
        early = reservation_service.list_reservations(
            self.settings, user_id=1, is_admin=False, date_to=upper
        )
        for row in early:
            self.assertLessEqual(str(row["res_date"]), upper)

    def test_offset_pagination(self) -> None:
        self._create(2)
        self._create(3)
        self._create(4)
        first = reservation_service.list_reservations(
            self.settings, user_id=1, is_admin=False, limit=2, offset=0
        )
        second = reservation_service.list_reservations(
            self.settings, user_id=1, is_admin=False, limit=2, offset=2
        )
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 1)
        self.assertFalse({row["id"] for row in first} & {row["id"] for row in second})


class LostFoundFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_all()
        self.settings = make_settings()

    def _create(self, title: str, location: str) -> None:
        lostfound_service.create_item(
            self.settings,
            1,
            {
                "kind": "found",
                "title": title,
                "description": "拾到物品，请失主认领",
                "location": location,
                "contact": "13800000000",
                "image_urls": [],
            },
        )

    def test_returns_list_contract(self) -> None:
        self._create("捡到校园卡", "图书馆")
        rows = lostfound_service.list_items(
            self.settings, user_id=1, is_admin=True, scope="mine"
        )
        self.assertIsInstance(rows, list)

    def test_keyword_matches_title_and_location(self) -> None:
        self._create("捡到校园卡", "图书馆一楼")
        self._create("捡到雨伞", "食堂二楼")
        by_title = lostfound_service.list_items(
            self.settings, user_id=1, is_admin=True, scope="mine", keyword="校园卡"
        )
        self.assertEqual(len(by_title), 1)
        by_location = lostfound_service.list_items(
            self.settings, user_id=1, is_admin=True, scope="mine", keyword="食堂"
        )
        self.assertEqual(len(by_location), 1)
        self.assertEqual(by_location[0]["title"], "捡到雨伞")

    def test_offset_pagination(self) -> None:
        for index in range(3):
            self._create(f"测试物品 {index}", "体育馆")
        first = lostfound_service.list_items(
            self.settings, user_id=1, is_admin=True, scope="mine", limit=2, offset=0
        )
        second = lostfound_service.list_items(
            self.settings, user_id=1, is_admin=True, scope="mine", limit=2, offset=2
        )
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 1)


if __name__ == "__main__":
    unittest.main()
