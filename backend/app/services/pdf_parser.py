"""PDF 解析（文字层）。

当前只处理**文字版 PDF**（PDF 里本身带可提取文本，大多数学校发布的 Word→PDF 属于这种）。
扫描件/图片版 PDF 没有文字层，需要 OCR（另装 OCR 引擎），见 README 的说明。

实现用 PyMuPDF（本机已装 1.28.2），对中文 CID 字体提取比 pypdf 更稳。
"""

from __future__ import annotations

from pathlib import Path

from app.services.rules_parser import ParsedDocument, parse_text


class PDFTextError(ValueError):
    """PDF 没有可提取的文字层（可能是扫描件，需要 OCR）。"""


def extract_pdf_text(data: bytes) -> str:
    """提取 PDF 每页文字，返回按页拼接的纯文本。"""
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - 环境缺包时的提示
        raise RuntimeError("未安装 PyMuPDF：请执行 pip install pymupdf") from exc

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        pages = [page.get_text("text") for page in document]
    finally:
        document.close()
    return "\n\n".join(pages)


def parse_pdf_bytes(
    data: bytes,
    filename: str,
    title: str | None = None,
) -> ParsedDocument:
    """把 PDF 字节解析成「章节 + 条款」结构。"""
    text = extract_pdf_text(data)
    if len(text.strip()) < 10:
        raise PDFTextError(
            "PDF 未提取到文字层（可能是扫描件/图片版），需要先做 OCR 才能入库。"
        )
    document = parse_text(text, fallback_title=Path(filename).stem)
    document.file_name = filename
    document.file_type = "pdf"
    if title and title.strip():
        document.title = title.strip()
    return document
