"""校规文档解析与切分。

首版支持 `.md` / `.txt`：
- `## 第X章 ...` 识别为章节；
- 以 `第N条` 开头的段落识别为一条校规，作为检索/图谱的最小单元（chunk）；
- 其余段落归入当前条目。

后续接 PDF/DOCX 时，只需把文件转成纯文本后复用 parse_text()。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CHAPTER_RE = re.compile(r"^##\s+(.+)$")
# 纯文本 / PDF 提取文本里的章节行（没有 Markdown 的 ## 前缀）
CHAPTER_LINE_RE = re.compile(r"^(第[一二三四五六七八九十百零〇0-9]+章.*)$")
ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百零〇0-9]+条)")
SUPPORTED_SUFFIXES = {".md", ".txt"}


@dataclass
class ParsedChunk:
    """一个可入库的最小校规片段。"""

    chapter: str
    article: str
    content: str
    chunk_index: int


@dataclass
class ParsedDocument:
    """一份校规文档的解析结果。"""

    title: str
    file_name: str
    file_path: str
    file_type: str
    chunks: list[ParsedChunk] = field(default_factory=list)


def parse_text(text: str, fallback_title: str = "未命名文档") -> ParsedDocument:
    """把纯文本解析成「章节 + 条目」结构。"""
    title = fallback_title
    chapter = "正文"
    article = ""
    buffer: list[str] = []
    chunks: list[ParsedChunk] = []

    def flush() -> None:
        nonlocal buffer, article
        content = "\n".join(buffer).strip()
        if content:
            chunks.append(
                ParsedChunk(
                    chapter=chapter,
                    article=article or f"第{len(chunks) + 1}段",
                    content=content,
                    chunk_index=len(chunks),
                )
            )
        buffer = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# ") and title == fallback_title:
            title = line[2:].strip()
            continue
        if line.startswith("> "):  # 说明性引用，不入库
            continue
        # 纯文本/PDF：首行若像标题（短、无句号、不是章节/条款），当作文档标题
        if (
            title == fallback_title
            and not chunks
            and not article
            and len(line) <= 60
            and "。" not in line
            and not CHAPTER_RE.match(line)
            and not CHAPTER_LINE_RE.match(line)
            and not ARTICLE_RE.match(line)
        ):
            title = line
            continue
        chapter_match = CHAPTER_RE.match(line) or CHAPTER_LINE_RE.match(line)
        if chapter_match:
            flush()
            chapter = line[2:].strip() if line.startswith("##") else line
            article = ""
            continue
        article_match = ARTICLE_RE.match(line)
        if article_match:
            flush()
            article = article_match.group(1)
            buffer = [line]
            continue
        buffer.append(line)
    flush()

    if not chunks and text.strip():
        # 兜底切分：PDF/OCR 文本没有「第N条」结构时，按长度 + 重叠切块，保证可检索
        chunks = _fallback_chunks(text)
    elif chunks:
        # 过长的条款（例如整段 OCR 文本）再拆成小块，避免单个 chunk 太大影响召回
        chunks = _split_oversized(chunks)

    return ParsedDocument(
        title=title,
        file_name="",
        file_path="",
        file_type="txt",
        chunks=chunks,
    )


def _split_oversized(
    chunks: list[ParsedChunk], max_chars: int = 1200, size: int = 800, overlap: int = 80
) -> list[ParsedChunk]:
    """把超长 chunk 拆分为带重叠的小块（保留原章节/条款号）。"""
    split: list[ParsedChunk] = []
    for chunk in chunks:
        if len(chunk.content) <= max_chars:
            split.append(chunk)
            continue
        start = 0
        part = 1
        while start < len(chunk.content):
            piece = chunk.content[start : start + size].strip()
            if piece:
                article = chunk.article if part == 1 else f"{chunk.article}-{part}"
                split.append(
                    ParsedChunk(
                        chapter=chunk.chapter,
                        article=article,
                        content=piece,
                        chunk_index=len(split),
                    )
                )
            if start + size >= len(chunk.content):
                break
            start += size - overlap
            part += 1
    return split


def _fallback_chunks(text: str, size: int = 600, overlap: int = 80) -> list[ParsedChunk]:
    """没有条款结构时的兜底切块（定长 + 重叠）。"""
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    chunks: list[ParsedChunk] = []
    start = 0
    while start < len(cleaned):
        piece = cleaned[start : start + size].strip()
        if piece:
            chunks.append(
                ParsedChunk(
                    chapter="正文",
                    article=f"第{len(chunks) + 1}段",
                    content=piece,
                    chunk_index=len(chunks),
                )
            )
        if start + size >= len(cleaned):
            break
        start += size - overlap
    return chunks


def parse_file(path: Path) -> ParsedDocument:
    """解析单个 .md / .txt 文件。"""
    text = path.read_text(encoding="utf-8")
    document = parse_text(text, fallback_title=path.stem)
    document.file_name = path.name
    document.file_path = str(path)
    document.file_type = path.suffix.lstrip(".").lower() or "txt"
    return document


def load_documents(directory: str | Path) -> list[ParsedDocument]:
    """加载目录下所有可解析的校规文档（按文件名排序，保证入库顺序稳定）。"""
    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"校规目录不存在：{root}")
    documents: list[ParsedDocument] = []
    for path in sorted(root.iterdir()):
        if path.name.lower() in {"readme.md", "readme.txt"}:
            continue
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            documents.append(parse_file(path))
    return documents
