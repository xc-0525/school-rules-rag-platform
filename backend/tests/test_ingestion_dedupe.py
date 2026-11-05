"""条款去重的纯函数测试（不依赖 MySQL/Qdrant）。

去重键的两个坑都在这里锁住：
1. PDF 解析的 content 带"第N条"前缀，md 不带 → 必须去前缀才能跨文档判重；
2. OCR 会在汉字/数字间插空格 → 必须去空白。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.ingestion import DedupeReport, _dedupe_key  # noqa: E402


class DedupeKeyTests(unittest.TestCase):
    def test_article_prefix_ignored(self) -> None:
        pdf_text = "第5条 宿舍楼晚上十一点锁门、熄灯，晚归者必须出示证件并进行登记。"
        md_text = "宿舍楼晚上十一点锁门、熄灯，晚归者必须出示证件并进行登记。"
        self.assertEqual(_dedupe_key("正文", pdf_text), _dedupe_key("", md_text))

    def test_chinese_article_number(self) -> None:
        self.assertEqual(_dedupe_key("", "第十条 学生应爱护公物。"), _dedupe_key("", "学生应爱护公物。"))

    def test_whitespace_and_ocr_spaces_ignored(self) -> None:
        self.assertEqual(
            _dedupe_key("第一章", "第1条 考核 成绩 记入 档案"),
            _dedupe_key("", "考核成绩记入档案"),
        )

    def test_reindexed_article_is_duplicate(self) -> None:
        # 同一条款在不同文档里编号不同，正文一致 → 应判为重复
        self.assertEqual(_dedupe_key("", "第1条 禁止饲养宠物"), _dedupe_key("", "第9条 禁止饲养宠物"))

    def test_different_content_not_equal(self) -> None:
        self.assertNotEqual(_dedupe_key("", "禁止饲养宠物"), _dedupe_key("", "禁止饲养家禽"))

    def test_empty_safe(self) -> None:
        self.assertEqual(_dedupe_key("", ""), "")
        self.assertEqual(_dedupe_key("", None), "")  # type: ignore[arg-type]


class DedupeReportTests(unittest.TestCase):
    def test_skipped_all_flag(self) -> None:
        report = DedupeReport(total=3, added=0, duplicates=3).as_dict()
        self.assertTrue(report["skipped_all"])
        self.assertEqual(report["duplicates"], 3)

    def test_partial_import(self) -> None:
        report = DedupeReport(
            total=9,
            added=6,
            duplicates=3,
            added_items=[{"label": "第1条"}],
        ).as_dict()
        self.assertFalse(report["skipped_all"])
        self.assertEqual(len(report["added_items"]), 1)
        self.assertEqual(report["duplicate_items"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
