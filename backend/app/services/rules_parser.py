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
        chapter_match = CHAPTER_RE.match(line)
        if chapter_match:
            flush()
            chapter = chapter_match.group(1).strip()
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

    return ParsedDocument(
        title=title,
        file_name="",
        file_path="",
        file_type="txt",
        chunks=chunks,
    )


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
