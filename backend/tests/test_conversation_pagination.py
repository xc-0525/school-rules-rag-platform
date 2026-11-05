"""会话历史分页 / 搜索的边界行为。

覆盖：默认时间倒序、翻页不重叠、恰好整页、越界 offset、搜索命中与空结果、
搜索与分页组合、跨用户隔离、标题大小写不敏感。
"""

from __future__ import annotations

import asyncio
import unittest

from app.services.persistence import MockPersistence


class ConversationPaginationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.persistence = MockPersistence()
        self._seed(12)

    def _seed(self, count: int) -> None:
        for i in range(1, count + 1):
            self.persistence._conversations[i] = {
                "id": i,
                "user_id": 1,
                "title": "图书馆开放时间" if i % 3 == 0 else f"宿舍问题 {i}",
                "created_at": f"2026-11-01 10:{i:02d}:00",
                "updated_at": f"2026-11-01 10:{i:02d}:00",
                "message_count": 2,
            }
        # 另一个用户的会话不应出现在列表里
        self.persistence._conversations[99] = {
            "id": 99,
            "user_id": 2,
            "title": "别人的会话",
            "created_at": "2026-11-02 09:00:00",
            "updated_at": "2026-11-02 09:00:00",
            "message_count": 2,
        }

    def _list(self, **kwargs):
        return asyncio.run(self.persistence.list_conversations(1, **kwargs))

    def test_default_order_is_updated_desc(self) -> None:
        items, total = self._list(limit=5)
        self.assertEqual(total, 12)
        self.assertEqual([item["id"] for item in items], [12, 11, 10, 9, 8])

    def test_pages_do_not_overlap(self) -> None:
        page1, _ = self._list(limit=5, offset=0)
        page2, _ = self._list(limit=5, offset=5)
        ids1 = {item["id"] for item in page1}
        ids2 = {item["id"] for item in page2}
        self.assertFalse(ids1 & ids2, "相邻两页不应出现重复会话")
        self.assertEqual(len(ids1 | ids2), 10)

    def test_exactly_full_page_has_no_next(self) -> None:
        items, total = self._list(limit=6, offset=6)
        self.assertEqual(len(items), 6)
        self.assertEqual(total, 12)
        # 恰好整页：用 total 判断，不能再让前端多请求一次空页
        self.assertFalse(6 + len(items) < total)

    def test_offset_beyond_total_returns_empty(self) -> None:
        items, total = self._list(limit=10, offset=100)
        self.assertEqual(items, [])
        self.assertEqual(total, 12)

    def test_last_page_partial(self) -> None:
        items, total = self._list(limit=10, offset=10)
        self.assertEqual(len(items), 2)
        self.assertEqual(total, 12)
        self.assertFalse(10 + len(items) < total)

    def test_keyword_filters_and_reports_total(self) -> None:
        items, total = self._list(limit=20, keyword="图书馆")
        self.assertEqual(total, 4)
        self.assertEqual(len(items), 4)
        self.assertTrue(all("图书馆" in item["title"] for item in items))

    def test_keyword_without_match(self) -> None:
        items, total = self._list(limit=20, keyword="不存在的主题")
        self.assertEqual(items, [])
        self.assertEqual(total, 0)

    def test_keyword_with_pagination(self) -> None:
        page1, total1 = self._list(limit=2, offset=0, keyword="图书馆")
        page2, total2 = self._list(limit=2, offset=2, keyword="图书馆")
        self.assertEqual((total1, total2), (4, 4))
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertFalse({i["id"] for i in page1} & {i["id"] for i in page2})

    def test_keyword_is_case_insensitive(self) -> None:
        self.persistence._conversations[50] = {
            "id": 50,
            "user_id": 1,
            "title": "Qdrant 与 Neo4j 的区别",
            "created_at": "2026-11-03 08:00:00",
            "updated_at": "2026-11-03 08:00:00",
            "message_count": 2,
        }
        items, total = self._list(limit=10, keyword="qdrant")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], 50)

    def test_other_user_isolated(self) -> None:
        items, total = self._list(limit=50)
        self.assertEqual(total, 12)
        self.assertNotIn(99, [item["id"] for item in items])


if __name__ == "__main__":
    unittest.main()
