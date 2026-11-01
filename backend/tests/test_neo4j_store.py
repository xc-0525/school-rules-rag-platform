"""Neo4j 图谱检索测试（Neo4j 不可用时自动跳过）。

前置条件：Neo4j 已启动，且执行过
    python scripts/ingest_rules.py --dir ../rules --reset
"""

from __future__ import annotations

import asyncio
import socket
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.stores import Neo4jGraphStore  # noqa: E402

NEO4J_URI = "bolt://127.0.0.1:7687"


def neo4j_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7687), timeout=1):
            return True
    except OSError:
        return False


@unittest.skipUnless(neo4j_available(), "Neo4j 未启动，跳过图谱检索测试")
class Neo4jGraphStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Neo4jGraphStore(uri=NEO4J_URI, user="", password="")

    def test_search_returns_graph_evidence(self) -> None:
        hits = asyncio.run(self.store.search_paths("宿舍晚上几点熄灯？", top_k=2))
        self.assertTrue(hits, "图谱检索为空，请先执行 scripts/ingest_rules.py")
        self.assertEqual(hits[0].type, "graph")
        # 不绑定具体条款号：语料可替换（当前为真实校规，条款用中文数字）
        self.assertRegex(hits[0].detail, r"第[一二三四五六七八九十百零〇0-9]+条")
        self.assertIn("熄灯", " ".join(hit.snippet for hit in hits))
        self.assertGreaterEqual(hits[0].score, 0.6)
        # 路径信息：文档 → 章节 → 条款 → 主题
        self.assertIn("主题：", hits[0].detail)

    def test_exam_query_hits_exam_rule(self) -> None:
        hits = asyncio.run(self.store.search_paths("考试带手机怎么处理？", top_k=2))
        self.assertTrue(hits)
        self.assertTrue(
            any("考试" in hit.snippet for hit in hits), "图谱应召回考试相关条款"
        )
        self.assertRegex(hits[0].detail, r"第[一二三四五六七八九十百零〇0-9]+条")

    def test_unrelated_query_returns_empty(self) -> None:
        hits = asyncio.run(self.store.search_paths("今天天气怎么样？", top_k=2))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
