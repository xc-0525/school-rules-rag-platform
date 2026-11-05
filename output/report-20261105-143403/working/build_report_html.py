#!/usr/bin/env python
"""把 Stage1 的 Markdown 转成符合 doc-formatter 规范的 HTML（学术报告版式）。

规范要点（见 doc-formatter §2.5）：
- 正文宋体 12pt + text-indent:2em；标题用 h1/h2 保留大纲级别
- 封面标题用 p 大字号（禁 h1，否则被目录收录）
- 封面与目录各自独立 section[role=cover]（目录页无页码）
- 正文 section 带 data-page-restart="1"
- @page 页脚页码；封面命名页无页码
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

RID = "report-20261105-143403"
BASE = Path(__file__).resolve().parents[3]  # working → report-xxx → output → 项目根
MD = BASE / "output" / RID / "stage1" / "final_draft.md"
OUT = BASE / "output" / RID / "stage2" / "formatted-校园服务智能问答系统实验报告.html"

TITLE = "校园服务智能问答系统的设计与实现"
SUBTITLE = "《人机交互软件工程》课程设计实验报告"

COVER_ROWS = [
    ("课程名称", "人机交互软件工程"),
    ("设计题目", TITLE),
    ("学　　院", "人工智能学院"),
    ("专业班级", "（待填写）"),
    ("学生姓名", "（待填写）"),
    ("学　　号", "（待填写）"),
    ("指导教师", "（待填写）"),
]


def inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def parse_markdown(md_text: str):
    """返回 (章节列表, 摘要内容, 关键词)。章节 = {level, title, anchor, blocks[]}"""
    lines = md_text.splitlines()
    chapters: list[dict] = []
    intro_before: list[str] = []
    abstract_html = ""
    keywords = ""
    i = 0
    current = None
    pending: list[str] = []

    def flush_paragraph(buf: list[str]) -> None:
        nonlocal abstract_html, keywords
        if not buf:
            return
        text = " ".join(x.strip() for x in buf if x.strip())
        if not text:
            return
        if text.startswith("**关键词**") or text.startswith("关键词"):
            keywords = re.sub(r"^\**关键词\**\s*[:：]?\s*", "", text)
            return
        target = current["blocks"] if current else intro_before
        target.append(("p", inline(text)))

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_paragraph(pending)
            pending = []
            i += 1
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            flush_paragraph(pending)
            pending = []
            header = [c.strip() for c in stripped.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            block = ("table", (header, rows))
            (current["blocks"] if current else intro_before).append(block)
            continue

        # 标题
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            flush_paragraph(pending)
            pending = []
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1:
                i += 1
                continue  # 文档标题只用于封面
            if level == 2:
                if text == "摘要":
                    current = {"level": 1, "title": text, "anchor": "abstract", "blocks": []}
                    chapters.append(current)
                    # 摘要段进入本章
                else:
                    current = {
                        "level": 1,
                        "title": text,
                        "anchor": f"section-{len([c for c in chapters if c['level'] == 1])}",
                        "blocks": [],
                    }
                    chapters.append(current)
            else:
                current = {
                    "level": 2,
                    "title": text,
                    "anchor": f"sub-{len(chapters)}",
                    "blocks": [],
                }
                chapters.append(current)
            i += 1
            continue

        # 列表
        if re.match(r"^[-*]\s+", stripped):
            flush_paragraph(pending)
            pending = []
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            (current["blocks"] if current else intro_before).append(("ul", [inline(x) for x in items]))
            continue

        pending.append(stripped)
        i += 1

    flush_paragraph(pending)

    # 摘要章节拆出（前两段为摘要正文，关键词单独一行）
    for ch in chapters:
        if ch["title"] == "摘要":
            parts = []
            for kind, payload in ch["blocks"]:
                if kind == "p":
                    parts.append(re.sub(r"<[^>]+>", "", payload))
            abstract_html = " ".join(parts)
            ch["blocks"] = []
            break
    return chapters, abstract_html, keywords


def render_blocks(blocks, p_class: str = "") -> str:
    out = []
    attr = f' class="{p_class}"' if p_class else ""
    style = "" if p_class else ' style="text-indent:2em"'
    for kind, payload in blocks:
        if kind == "p":
            out.append(f"<p{attr}{style}>{payload}</p>")
        elif kind == "ul":
            lis = "".join(f"<li>{x}</li>" for x in payload)
            out.append(f'<ul style="margin:0 0 0.8em 1.6em">{lis}</ul>')
        elif kind == "table":
            header, rows = payload
            th = "".join(f"<th>{inline(c)}</th>" for c in header)
            trs = []
            for row in rows:
                tds = "".join(f"<td>{inline(c)}</td>" for c in row)
                trs.append(f"<tr>{tds}</tr>")
            out.append(
                '<table class="three-line-table"><thead><tr>'
                + th
                + "</tr></thead><tbody>"
                + "".join(trs)
                + "</tbody></table>"
            )
    return "\n".join(out)


STYLE = """
@page {
  @bottom-center { content: "第 " counter(page) " 页"; }
}
@page cover {
  @bottom-center { content: none; }
}
section[role="cover"] { page: cover; }

:root {
  --ff-heading: "黑体", SimHei, sans-serif;
  --ff-body: "宋体", SimSun, serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "SimSun", "宋体", serif;
  font-size: 12pt;
  line-height: 1.6;
  color: #000;
  background: #fff;
  padding: 2.5cm;
}
h1, h2, h3 { font-family: "SimHei", "黑体", sans-serif; font-weight: 700; }
h1 { font-size: 16pt; margin: 1.2em 0 0.7em; page-break-after: avoid; }
h2 { font-size: 14pt; margin: 1em 0 0.6em; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 0.8em 0 0.4em; }
p { text-indent: 2em; margin-bottom: 0.6em; text-align: justify; }
ul { margin: 0 0 0.8em 1.6em; }
li { margin-bottom: 0.2em; }
code { font-family: Consolas, "Courier New", monospace; font-size: 10.5pt; }
.cover { text-align: center; }
.cover-course { font-size: 14pt; margin-top: 2cm; letter-spacing: 2px; }
.cover-title { font-size: 26pt; font-weight: 700; margin: 2.2cm 0 0.6cm; font-family: "SimHei", "黑体", sans-serif; }
.cover-subtitle { font-size: 16pt; margin-bottom: 2.4cm; color: #333; }
.cover-info { border-collapse: collapse; margin: 0 auto 2.6cm; }
.cover-info td { padding: 0.28em 0.9em; font-size: 13pt; border: none; }
.cover-info td:first-child { text-align: right; font-family: "SimHei", "黑体", sans-serif; white-space: nowrap; }
.cover-info td:last-child { text-align: left; border-bottom: 1px solid #000; min-width: 4.6cm; }
.cover-date { font-size: 13pt; }
.doc-toc { margin-bottom: 1em; }
.toc-title { text-align: center; font-size: 16pt; font-weight: 700; font-family: "SimHei", "黑体", sans-serif; margin: 0 0 0.8em; text-indent: 0; }
.toc-list, .toc-list ol { list-style: none; list-style-type: none; padding-left: 0; }
.toc-list > li { margin-bottom: 0.45em; font-size: 12pt; text-indent: 0; }
.toc-list ol { padding-left: 1.6em; margin-top: 0.25em; }
.toc-list ol li { margin-bottom: 0.2em; font-size: 11pt; }
.toc-list a { color: #000; text-decoration: none; }
.abstract-box { border-left: 3px solid #01579b; padding: 0.3em 0 0.3em 0.9em; margin: 0.6em 0 1.2em; }
.abstract-box p { text-indent: 0; }
.abstract-head { font-weight: 700; font-family: "SimHei", "黑体", sans-serif; margin-bottom: 0.4em; }
.keywords { text-indent: 0; margin-top: 0.4em; }
.three-line-table { border-collapse: collapse; width: 100%; margin: 0.5em 0 1em; font-size: 10.5pt; }
.three-line-table thead tr:first-child { border-top: 2px solid #000; }
.three-line-table thead tr:last-child { border-bottom: 1px solid #000; }
.three-line-table tbody tr:last-child { border-bottom: 2px solid #000; }
.three-line-table td, .three-line-table th { padding: 0.35em 0.6em; text-align: left; vertical-align: top; }
.three-line-table th { font-family: "SimHei", "黑体", sans-serif; font-weight: 700; }
.ref-item { text-indent: 0; font-size: 10.5pt; margin-bottom: 0.35em; }
"""


def main() -> int:
    md_text = MD.read_text(encoding="utf-8")
    chapters, abstract, keywords = parse_markdown(md_text)

    top = [c for c in chapters if c["level"] == 1 and c["title"] != "摘要"]

    # 目录：一级章节 + 二级小节
    toc_items = []
    for ch in top:
        anchor = ch["anchor"]
        subs = [c for c in chapters if c["level"] == 2]
        toc_items.append(f'<li><a href="#{anchor}">{html.escape(ch["title"])}</a></li>')
    # 二级条目按出现顺序挂到父章节下（简单处理：按块顺序重建）
    toc_html = []
    idx = 0
    for ch in chapters:
        if ch["level"] == 1 and ch["title"] != "摘要":
            idx += 1
            children = []
            # 收集紧随其后的二级标题
            pos = chapters.index(ch)
            for nxt in chapters[pos + 1 :]:
                if nxt["level"] == 1:
                    break
                children.append(nxt)
            sub_html = ""
            if children:
                sub_html = "<ol>" + "".join(
                    f'<li><a href="#{c.get("anchor", "")}">{html.escape(c["title"])}</a></li>'
                    for c in children
                ) + "</ol>"
            toc_html.append(f'<li><a href="#{ch["anchor"]}">{html.escape(ch["title"])}</a>{sub_html}</li>')
    toc_block = "".join(toc_html)

    # 正文
    body_parts = []
    for ch in chapters:
        if ch["title"] == "摘要":
            continue
        tag = "h1" if ch["level"] == 1 else "h2"
        anchor = f' id="{ch["anchor"]}"' if ch.get("anchor") else ""
        body_parts.append(f"<{tag}{anchor}>{html.escape(ch['title'])}</{tag}>")
        body_parts.append(
            render_blocks(ch["blocks"], p_class="ref-item" if "参考文献" in ch["title"] else "")
        )

    cover_rows = "".join(
        f"<tr><td>{k}</td><td>{html.escape(v)}</td></tr>" for k, v in COVER_ROWS
    )

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="docx-page-size" content="A4">
<title>{TITLE}</title>
<style>{STYLE}</style>
</head>
<body>
<section role="cover">
  <div class="cover">
    <p class="cover-course">《人机交互软件工程》课程设计</p>
    <p class="cover-title">{TITLE}</p>
    <p class="cover-subtitle">课程设计实验报告书</p>
    <table class="cover-info">{cover_rows}</table>
    <p class="cover-date">2026 年 11 月</p>
  </div>
</section>

<section role="cover">
  <nav class="doc-toc" aria-label="文档目录">
    <p class="toc-title">目　　录</p>
    <ol class="toc-list">{toc_block}</ol>
  </nav>
</section>

<section role="body" data-page-restart="1">
  <h1 id="abstract">摘要</h1>
  <div class="abstract-box">
    <p class="abstract-head">摘　要</p>
    <p>{html.escape(abstract)}</p>
    <p class="keywords"><strong>关键词：</strong>{html.escape(keywords)}</p>
  </div>
  {''.join(body_parts)}
</section>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc, encoding="utf-8")
    print(f"生成: {OUT} ({len(doc)} chars)")
    print(f"章节 {len(top)} 个，小节 {len([c for c in chapters if c['level'] == 2])} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
