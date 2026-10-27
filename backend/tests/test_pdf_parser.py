"""PDF 解析测试：用 PyMuPDF 现场生成一个中文文字版 PDF，验证提取与切分。

不依赖外部文件/网络；扫描件 OCR 不在本测试范围（需要额外 OCR 引擎）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.pdf_parser import PDFTextError, extract_pdf_text, parse_pdf_bytes  # noqa: E402
from app.services.ocr_parser import (  # noqa: E402
    OCRUnavailableError,
    normalize_ocr_text,
    ocr_available,
    ocr_pdf_bytes,
)
from app.services.rules_parser import parse_text  # noqa: E402


def make_pdf(text: str) -> bytes:
    """用 PyMuPDF 内置简体中文字体生成文字版 PDF。"""
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 72), text, fontname="china-s", fontsize=12)
    data = document.tobytes()
    document.close()
    return data


SAMPLE_TEXT = (
    "XX大学校园服务指南\n"
    "第一章 图书馆服务\n"
    "第1条 图书馆开放时间：周一至周五 08:00-22:00。\n"
    "第2条 本科生每次可借阅图书10册，借期30天。"
)


class PDFParserTests(unittest.TestCase):
    def test_extract_chinese_text(self) -> None:
        data = make_pdf(SAMPLE_TEXT)
        text = extract_pdf_text(data)
        self.assertIn("图书馆开放时间", text)
        self.assertIn("第1条", text)

    def test_parse_pdf_bytes(self) -> None:
        data = make_pdf(SAMPLE_TEXT)
        document = parse_pdf_bytes(data, "测试手册.pdf")
        self.assertEqual(document.file_type, "pdf")
        self.assertEqual(len(document.chunks), 2)
        self.assertEqual(document.chunks[0].article, "第1条")
        self.assertIn("图书馆开放时间", document.chunks[0].content)

    def test_scanned_pdf_raises(self) -> None:
        """没有文字层的 PDF（空白页）应提示需要 OCR。"""
        import pymupdf

        document = pymupdf.open()
        document.new_page()  # 空白页 = 无文字层
        data = document.tobytes()
        document.close()
        with self.assertRaises(PDFTextError) as ctx:
            parse_pdf_bytes(data, "扫描件.pdf")
        self.assertIn("OCR", str(ctx.exception))

    def test_fallback_chunking_without_articles(self) -> None:
        """PDF/OCR 文本没有「第N条」时，按定长+重叠兜底切块。"""
        document = parse_text("校规说明文字。" * 300)
        self.assertGreaterEqual(len(document.chunks), 2)
        self.assertEqual(document.chunks[0].chapter, "正文")
        self.assertTrue(all(chunk.content for chunk in document.chunks))
        self.assertTrue(all(len(chunk.content) <= 1200 for chunk in document.chunks))

    @unittest.skipIf(ocr_available(), "本机已安装 OCR 引擎，跳过缺失依赖提示测试")
    def test_ocr_unavailable_message(self) -> None:
        with self.assertRaises(OCRUnavailableError) as ctx:
            ocr_pdf_bytes(b"%PDF-1.4 fake", "扫描件.pdf")
        self.assertIn("OCR", str(ctx.exception))

    def test_normalize_ocr_text(self) -> None:
        """OCR 在中文/数字间插入的空格应被去掉，保证「第N条」可被解析。"""
        raw = "第1 条 图书馆 开放时间：1 0 册"
        self.assertEqual(normalize_ocr_text(raw), "第1条图书馆开放时间：10册")


if __name__ == "__main__":
    unittest.main(verbosity=2)
