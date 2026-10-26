"""扫描件 PDF OCR（可选依赖，装好引擎后自动启用）。

后端上传 PDF 时的策略：
1. 先走 pdf_parser（文字层提取，零额外依赖）；
2. 如果 PDF 没有文字层（扫描件/图片版），自动尝试 OCR；
3. 没装 OCR 引擎时给出明确的安装提示，而不是静默失败。

推荐 OCR 引擎：RapidOCR（onnxruntime，中文友好、无需外部 exe）：
    pip install rapidocr_onnxruntime pillow
备选：pytesseract + Tesseract-OCR（需另装 exe 与 chi_sim 中文语言包）。
"""

from __future__ import annotations

from pathlib import Path

from app.services.rules_parser import ParsedDocument, parse_text


class OCRUnavailableError(RuntimeError):
    """未安装 OCR 引擎。"""


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return True
    except ImportError:
        return False


def ocr_pdf_bytes(
    data: bytes,
    filename: str,
    title: str | None = None,
    dpi: int = 200,
) -> ParsedDocument:
    """对扫描件 PDF 逐页 OCR，再把文本按「章节/条款」解析。"""
    try:
        import numpy as np
        import pymupdf
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # pragma: no cover - 取决于本机安装
        raise OCRUnavailableError(
            "未安装 OCR 引擎，扫描件 PDF 无法入库。请执行："
            "pip install rapidocr_onnxruntime pillow（推荐），"
            "或安装 pytesseract + Tesseract-OCR 中文包。"
        ) from exc

    engine = RapidOCR()
    document = pymupdf.open(stream=data, filetype="pdf")
    page_texts: list[str] = []
    try:
        for page in document:
            pixmap = page.get_pixmap(dpi=dpi)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            if pixmap.n == 4:  # RGBA → RGB
                image = image[:, :, :3]
            result, _ = engine(image)
            lines = [item[1] for item in result] if result else []
            page_texts.append("\n".join(lines))
    finally:
        document.close()

    text = "\n\n".join(page_texts).strip()
    if not text:
        raise OCRUnavailableError("OCR 未识别到文字，请检查 PDF 清晰度或换用文字版 PDF。")

    parsed = parse_text(text, fallback_title=Path(filename).stem)
    parsed.file_name = filename
    parsed.file_type = "pdf"
    if title and title.strip():
        parsed.title = title.strip()
    return parsed
