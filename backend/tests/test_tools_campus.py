"""校园服务只读工具测试。

本机 Python 3.14 装不上 pytest（无 wheel），统一用 unittest.IsolatedAsyncioTestCase，
与项目其余 138 项测试保持同一运行方式。

覆盖：只读不落库、按用户隔离、只返回已公示的失物、余量查询、消融开关。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents import tools as tools_module  # noqa: E402
from app.agents.tools import ToolRunner, active_tool_definitions  # noqa: E402
from app.services import lostfound as lf_service  # noqa: E402
from app.services import repairs as repair_service  # noqa: E402
from app.services import reservations as res_service  # noqa: E402
from tests.test_review_fixes import make_settings  # noqa: E402

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


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


def _lost_payload(**overrides) -> dict:
    payload = {
        "kind": "found",
        "title": "捡到一张校园卡",
        "description": "在图书馆三楼捡到校园卡一张，请失主到服务台认领。",
        "location": "图书馆三楼",
        "contact": "QQ 123456",
    }
    payload.update(overrides)
    return payload


class CampusToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        repair_service.clear_mock_repairs()
        res_service.clear_mock_reservations()
        lf_service.clear_mock_lostfound()
        self.settings = make_settings()
        self.runner = ToolRunner(services=None, settings=self.settings)

    async def _run(self, name: str, arguments: dict, user_id: int = 1):
        return await self.runner.run(
            name, arguments, user_id=user_id, conversation_id=None, run_id="test"
        )

    async def test_repair_tool_is_read_only(self) -> None:
        result = await self._run("query_repair_status", {})
        self.assertTrue(result.success)
        self.assertEqual(result.payload["repairs"], [])
        # 关键：查询不得产生任何写入
        self.assertEqual(repair_service.list_repairs(self.settings, user_id=1, is_admin=False), [])

    async def test_repair_tool_isolated_by_user(self) -> None:
        repair_service.create_repair(self.settings, 1, _repair_payload())
        mine = await self._run("query_repair_status", {}, user_id=1)
        self.assertEqual(len(mine.payload["repairs"]), 1)
        other = await self._run("query_repair_status", {}, user_id=2)
        self.assertEqual(other.payload["repairs"], [], "工具只能查到本人数据")

    async def test_repair_tool_status_filter_and_remark(self) -> None:
        created = repair_service.create_repair(self.settings, 1, _repair_payload())
        repair_service.transition_repair(
            self.settings, created["id"], 9, is_admin=True, action="process", remark="已派师傅"
        )
        result = await self._run("query_repair_status", {"status": "processing"})
        self.assertEqual(len(result.payload["repairs"]), 1)
        self.assertIn("已派师傅", result.summary)
        self.assertIn("处理中", result.summary)
        # 非法 status 被归零，不抛错
        fallback = await self._run("query_repair_status", {"status": "magic"})
        self.assertEqual(len(fallback.payload["repairs"]), 1)

    async def test_lost_found_only_public(self) -> None:
        published = lf_service.create_item(self.settings, 1, _lost_payload())
        pending = lf_service.create_item(self.settings, 1, _lost_payload(title="捡到一把钥匙"))
        lf_service.review_item(self.settings, published["id"], 9, action="approve")

        result = await self._run("query_lost_found", {})
        items = result.payload["items"]
        self.assertEqual([item["id"] for item in items], [published["id"]])
        self.assertNotIn(pending["id"], [item["id"] for item in items], "待审条目不应对模型可见")

        hit = await self._run("query_lost_found", {"keyword": "校园卡"})
        self.assertEqual(len(hit.payload["items"]), 1)
        miss = await self._run("query_lost_found", {"keyword": "不存在的物品XYZ"})
        self.assertEqual(miss.payload["items"], [])

    async def test_location_availability(self) -> None:
        result = await self._run("query_location_availability", {"location": "研讨间", "date": TOMORROW})
        self.assertTrue(result.success)
        self.assertTrue(result.payload["locations"], "种子位置应命中")
        first_free = result.payload["locations"][0]["free_slots"]
        self.assertEqual(len(first_free), 3, "未预约时三个时段都可用")

        # 预定一个时段后再查，该时段应从 free_slots 消失
        locations = res_service.list_locations(self.settings)
        target = next(loc for loc in locations if "研讨间" in loc["name"])
        res_service.create_reservation(
            self.settings, 1, location_id=target["id"], res_date=TOMORROW, slot="morning"
        )
        again = await self._run(
            "query_location_availability", {"location": target["name"], "date": TOMORROW}
        )
        self.assertIn("下午", again.summary)
        self.assertNotIn("上午 08:00-12:00", again.summary, "已占用时段不应出现在可预约列表")

    async def test_availability_bad_date_degrades(self) -> None:
        # 昨天不在可预约范围：工具不抛异常，给出说明性结果
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        result = await self._run(
            "query_location_availability", {"location": "自习", "date": yesterday}
        )
        self.assertTrue(result.success)
        self.assertEqual(result.payload["locations"], [])
        self.assertIn("不在可预约范围内", result.summary)

    async def test_my_reservations_tool(self) -> None:
        locations = res_service.list_locations(self.settings)
        res_service.create_reservation(
            self.settings, 1, location_id=locations[0]["id"], res_date=TOMORROW, slot="afternoon"
        )
        mine = await self._run("query_my_reservations", {}, user_id=1)
        self.assertEqual(len(mine.payload["reservations"]), 1)
        self.assertIn("已预约", mine.summary)
        other = await self._run("query_my_reservations", {}, user_id=2)
        self.assertEqual(other.payload["reservations"], [])

    async def test_reservation_draft_is_not_written(self) -> None:
        """预约草稿只组装 payload，不落库；确认动作由前端按钮触发。"""
        result = await self._run(
            "create_reservation_draft",
            {"location": "研讨间 201", "date": TOMORROW, "slot": "下午"},
            user_id=1,
        )
        self.assertTrue(result.success, result.summary)
        payload = result.payload
        self.assertTrue(payload["requires_confirmation"])
        self.assertEqual(payload["action"], "create_reservation")
        self.assertEqual(payload["slot"], "afternoon")
        self.assertEqual(payload["res_date"], TOMORROW)
        self.assertIn("尚未占用座位", result.summary)
        # 关键：不产生任何预约记录
        self.assertEqual(
            res_service.list_reservations(self.settings, user_id=1, is_admin=False), []
        )

    async def test_reservation_draft_conflict_suggests_free_slot(self) -> None:
        locations = res_service.list_locations(self.settings)
        target = next(loc for loc in locations if "研讨间 201" in loc["name"])
        res_service.create_reservation(
            self.settings, 9, location_id=target["id"], res_date=TOMORROW, slot="morning"
        )
        result = await self._run(
            "create_reservation_draft",
            {"location": "研讨间 201", "date": TOMORROW, "slot": "morning"},
            user_id=1,
        )
        self.assertFalse(result.success)
        self.assertIn("已被约满", result.summary)
        self.assertIn("可改为", result.summary)

    async def test_reservation_draft_rejects_bad_date_and_quota(self) -> None:
        past = (date.today() - timedelta(days=1)).isoformat()
        bad = await self._run(
            "create_reservation_draft",
            {"location": "图书馆", "date": past, "slot": "morning"},
            user_id=1,
        )
        self.assertFalse(bad.success)
        self.assertIn("只能预约", bad.summary)

        # 占满 3 个有效预约后再生成草稿 → 提示先取消
        locations = res_service.list_locations(self.settings)
        for offset, slot in enumerate(("morning", "afternoon", "evening")):
            res_service.create_reservation(
                self.settings,
                1,
                location_id=locations[0]["id"],
                res_date=(date.today() + timedelta(days=1 + offset)).isoformat(),
                slot=slot,
            )
        full = await self._run(
            "create_reservation_draft",
            {"location": "图书馆一楼自习区", "date": TOMORROW, "slot": "morning"},
            user_id=1,
        )
        self.assertFalse(full.success)
        self.assertIn("最多同时保留", full.summary)

    # ------------------------------------------------ 失物招领发布（草稿 → 确认）
    async def test_lost_found_draft_is_not_written(self) -> None:
        result = await self._run(
            "create_lost_found_draft",
            {
                "kind": "lost",
                "title": "学生证",
                "description": "昨天在桂园三楼丢失学生证一张，姓名徐某某，有捡到的同学请联系我。",
                "location": "桂园三楼",
                "contact": "13800000000",
            },
        )
        self.assertTrue(result.success)
        self.assertEqual(result.payload["action"], "create_lost_found")
        self.assertTrue(result.payload["requires_confirmation"])
        self.assertEqual(result.payload["kind"], "lost")
        # 草稿阶段不落库，避免"没确认就占用了公示栏"
        self.assertEqual(lf_service.list_items(self.settings, user_id=1, is_admin=False, scope="mine"), [])

    async def test_lost_found_draft_requires_contact_and_description(self) -> None:
        no_contact = await self._run(
            "create_lost_found_draft",
            {
                "kind": "lost",
                "title": "学生证",
                "description": "昨天在桂园三楼丢失学生证一张，有捡到的同学请联系我。",
                "location": "桂园三楼",
                "contact": "",
            },
        )
        self.assertFalse(no_contact.success)
        self.assertIn("联系方式", no_contact.summary)

        short = await self._run(
            "create_lost_found_draft",
            {
                "kind": "lost",
                "title": "学生证",
                "description": "丢了",
                "location": "桂园三楼",
                "contact": "13800000000",
            },
        )
        self.assertFalse(short.success)
        self.assertIn("描述", short.summary)

        bad_kind = await self._run(
            "create_lost_found_draft",
            {**_lost_payload(), "kind": "unknown"},
        )
        self.assertFalse(bad_kind.success)

    async def test_my_lost_found_shows_review_status(self) -> None:
        item = lf_service.create_item(self.settings, 1, _lost_payload())
        mine = await self._run("query_my_lost_found", {}, user_id=1)
        self.assertTrue(mine.success)
        self.assertIn("待审核", mine.summary)
        self.assertIn(f"#{item['id']}", mine.summary)

        # 审批通过后状态变化
        lf_service.review_item(self.settings, item["id"], 9, action="approve")
        after = await self._run("query_my_lost_found", {}, user_id=1)
        self.assertIn("已公示", after.summary)

        # 按用户隔离
        other = await self._run("query_my_lost_found", {}, user_id=2)
        self.assertEqual(other.payload["items"], [])

    # ---------------------------------------------------------- 报修提交（草稿）
    async def test_repair_draft_is_not_written(self) -> None:
        result = await self._run("create_repair_draft", _repair_payload())
        self.assertTrue(result.success)
        self.assertEqual(result.payload["action"], "create_repair")
        self.assertEqual(result.payload["building"], "12栋")
        self.assertEqual(
            repair_service.list_repairs(self.settings, user_id=1, is_admin=False), []
        )

    async def test_repair_draft_precheck_blocks_duplicate(self) -> None:
        repair_service.create_repair(self.settings, 1, _repair_payload())
        again = await self._run("create_repair_draft", _repair_payload())
        self.assertFalse(again.success, "同房间同类型 24h 内重复应在草稿阶段就拦住")
        self.assertIn("已提交过", again.summary)

    async def test_repair_draft_rejects_missing_fields(self) -> None:
        no_room = await self._run("create_repair_draft", _repair_payload(room=""))
        self.assertFalse(no_room.success)
        self.assertIn("楼栋和房间号", no_room.summary)

    # ------------------------------------------------------ 取消预约（草稿）
    async def test_cancel_reservation_draft_is_not_cancelled(self) -> None:
        locations = res_service.list_locations(self.settings)
        created = res_service.create_reservation(
            self.settings, 1, location_id=locations[0]["id"], res_date=TOMORROW, slot="afternoon"
        )
        result = await self._run("cancel_reservation_draft", {}, user_id=1)
        self.assertTrue(result.success)
        self.assertEqual(result.payload["action"], "cancel_reservation")
        self.assertEqual(result.payload["reservation_id"], created["id"])
        # 未确认前仍是有效预约
        active = res_service.list_reservations(
            self.settings, user_id=1, is_admin=False, status="reserved"
        )
        self.assertEqual(len(active), 1)

    async def test_cancel_reservation_draft_needs_unique_target(self) -> None:
        locations = res_service.list_locations(self.settings)
        for slot in ("morning", "afternoon"):
            res_service.create_reservation(
                self.settings, 1, location_id=locations[0]["id"], res_date=TOMORROW, slot=slot
            )
        ambiguous = await self._run("cancel_reservation_draft", {}, user_id=1)
        self.assertFalse(ambiguous.success)
        self.assertIn("请指定日期或时段", ambiguous.summary)

        picked = await self._run("cancel_reservation_draft", {"slot": "morning"}, user_id=1)
        self.assertTrue(picked.success)
        self.assertIn("上午 08:00-12:00", picked.payload["description"])

    # ------------------------------------------------------ 失物结案（草稿）
    async def test_resolve_lost_found_draft_only_published(self) -> None:
        pending = lf_service.create_item(self.settings, 1, _lost_payload())
        blocked = await self._run("resolve_lost_found_draft", {}, user_id=1)
        self.assertFalse(blocked.success)
        self.assertIn("待审核", blocked.summary)

        lf_service.review_item(self.settings, pending["id"], 9, action="approve")
        result = await self._run("resolve_lost_found_draft", {}, user_id=1)
        self.assertTrue(result.success)
        self.assertEqual(result.payload["action"], "resolve_lost_found")
        self.assertEqual(result.payload["item_id"], pending["id"])
        # 未确认前状态不变
        self.assertEqual(
            lf_service.get_item(self.settings, pending["id"], 1, is_admin=False)["status"], "published"
        )


    async def test_disabled_tool_not_executed(self) -> None:
        """关闭开关后：下发定义被裁掉，模型即便幻觉出工具名也不执行。"""
        self.settings.campus_tools_enabled = False
        result = await self._run("query_repair_status", {})
        self.assertFalse(result.success)
        self.assertIn("未启用", result.summary)
        self.assertTrue(repair_service.list_repairs(self.settings, user_id=1, is_admin=False) == [])


class ToolDefinitionTests(unittest.TestCase):
    def test_ablation_switch(self) -> None:
        settings = make_settings()
        names = [d["function"]["name"] for d in active_tool_definitions(settings)]
        self.assertEqual(len(names), 13)
        for name in tools_module.CAMPUS_TOOL_NAMES:
            self.assertIn(name, names)

        settings.campus_tools_enabled = False
        names = [d["function"]["name"] for d in active_tool_definitions(settings)]
        self.assertEqual(len(names), 3)
        for name in tools_module.CAMPUS_TOOL_NAMES:
            self.assertNotIn(name, names)

    def test_prompt_trimmed_when_disabled(self) -> None:
        from app.agents.prompts import tool_system_prompt

        settings = make_settings()
        enabled_prompt = tool_system_prompt(settings)
        self.assertIn("query_lost_found", enabled_prompt)
        settings.campus_tools_enabled = False
        disabled_prompt = tool_system_prompt(settings)
        self.assertNotIn("query_lost_found", disabled_prompt)
        self.assertNotIn("query_repair_status", disabled_prompt)
        self.assertIn("search_rules", disabled_prompt, "基础工具说明应保留")

    def test_ticket_intent_keywords_not_triggered_by_queries(self) -> None:
        # 查询类问法不再命中建单意图（原关键词表含"报修""失物"）
        for question in (
            "我的报修到哪了",
            "上周报修处理了吗",
            "有人捡到校园卡吗",
            "明天图书馆还有位置吗",
        ):
            self.assertFalse(
                tools_module._has_ticket_intent(question),
                f"查询不应命中建单意图：{question}",
            )
        # 明确的申请/投诉仍命中
        for question in ("我要报修", "申请报修", "食堂服务太差我要投诉"):
            self.assertTrue(tools_module._has_ticket_intent(question))


if __name__ == "__main__":
    unittest.main(verbosity=2)
