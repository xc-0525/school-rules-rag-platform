"""校规解析器单元测试（不依赖任何外部服务）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.rules_parser import load_documents, parse_file, parse_text  # noqa: E402

SAMPLE_DOC = BACKEND_DIR.parent / "rules" / "学生手册节选.md"


class RulesParserTests(unittest.TestCase):
    def test_parse_sample_document(self) -> None:
        self.assertTrue(SAMPLE_DOC.exists(), f"样例校规文档不存在：{SAMPLE_DOC}")
        document = parse_file(SAMPLE_DOC)
        self.assertEqual(document.title, "XX大学学生手册（节选）")
        self.assertEqual(document.file_type, "md")
        self.assertEqual(len(document.chunks), 26)
        chapters = {chunk.chapter for chunk in document.chunks}
        self.assertIn("第三章 学生宿舍管理", chapters)
        self.assertIn("第四章 考试纪律与学术规范", chapters)
        articles = [chunk.article for chunk in document.chunks]
        self.assertEqual(articles[0], "第1条")
        self.assertEqual(articles[-1], "第26条")
        self.assertTrue(all(chunk.content.strip() for chunk in document.chunks))

    def test_parse_text_and_title(self) -> None:
        text = "# 测试手册\n\n## 第一章 总则\n\n第1条 第一条内容。\n\n第2条 第二条内容。\n"
        document = parse_text(text)
        self.assertEqual(document.title, "测试手册")
        self.assertEqual(len(document.chunks), 2)
        self.assertEqual(document.chunks[1].article, "第2条")
        self.assertEqual(document.chunks[0].chapter, "第一章 总则")

    def test_load_documents_skips_readme(self) -> None:
        documents = load_documents(BACKEND_DIR.parent / "rules")
        names = [document.file_name for document in documents]
        self.assertNotIn("README.md", names)
        self.assertTrue(any(name.endswith(".md") for name in names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
