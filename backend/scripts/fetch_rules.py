#!/usr/bin/env python
"""抓取武昌理工学院官网公开的校纪校规，清洗成可直接入库的 Markdown。

背景：官网的校规分散在各子站（学生工作部 / 教务处 / 图书馆 / 各学院），
正文是网页 HTML，没有 `## 第X章` 和 `第N条` 这类结构标记，
直接入库会导致所有条款落到"正文"章节、引用无法定位。

本脚本做四件事：
1. 抓取（带本地缓存，`--force` 可强制刷新）；
2. 从三种已知模板中抽取正文（`v_news_content` / `class="article"` / 表格型 FAQ）；
3. 清洗：去页面元信息、合并被 `<span>` 打散的碎片、把编号项规整成章/条；
4. 输出 `rules/wut/*.md` + `_sources.json`（来源、抓取时间、条目数）。

用法：
    python scripts/fetch_rules.py                 # 用缓存，只重新清洗
    python scripts/fetch_rules.py --force         # 重新下载
    python scripts/fetch_rules.py --dry-run       # 只打印解析统计，不落盘
    python scripts/fetch_rules.py --only 01,07    # 只处理指定编号
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT = BACKEND_DIR.parent / "rules" / "wut"
DEFAULT_CACHE = BACKEND_DIR / ".cache" / "rules_html"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# ---------------------------------------------------------------------------
# 源清单：只收官网公开页面。同一制度存在多版本时分别保留，靠标题与来源区分。
# mode: auto=按内容自动判断；table=问答表格
# ---------------------------------------------------------------------------
SOURCES: list[dict[str, str]] = [
    {
        "id": "01",
        "name": "学生违纪违规处理规定",
        "title": "武昌理工学院学生违纪违规处理规定",
        "url": "https://xgc.wut.edu.cn/info/1831/35071.htm",
        "category": "违纪处分",
        "mode": "auto",
    },
    {
        "id": "02",
        "name": "普通高等学校学生管理规定",
        "title": "普通高等学校学生管理规定（教育部令第41号）",
        "url": "https://xgc.wut.edu.cn/info/1821/7711.htm",
        "category": "上位法规",
        "mode": "auto",
    },
    {
        "id": "03",
        "name": "学生学籍管理规定本科",
        "title": "武昌理工学院学生学籍管理规定（本科）",
        "url": "https://wbs.wut.edu.cn/info/3021/118881.htm",
        "category": "学籍管理",
        "mode": "auto",
    },
    {
        "id": "04",
        "name": "学生学籍管理规定专科",
        "title": "武昌理工学院学生学籍管理规定（专科）",
        "url": "https://music.wut.edu.cn/info/1291/2321.htm",
        "category": "学籍管理",
        "mode": "auto",
    },
    {
        "id": "05",
        "name": "学生管理规定总则部分",
        "title": "武昌理工学院学生管理规定（总则部分）",
        "url": "https://tw.wut.edu.cn/info/1251/1291.htm",
        "category": "学生管理",
        "mode": "auto",
    },
    {
        "id": "06",
        "name": "学生管理规定学籍与奖惩部分",
        "title": "武昌理工学院学生管理规定（学籍异动与奖惩部分）",
        "url": "https://tw.wut.edu.cn/info/1251/1281.htm",
        "category": "学生管理",
        "mode": "auto",
        "note": "官网该页从第二十五条起，前三章未公开，属节选",
    },
    {
        "id": "07",
        "name": "学生守则",
        "title": "武昌理工学院学生守则",
        "url": "https://nurse.wut.edu.cn/info/1651/7611.htm",
        "category": "行为规范",
        "mode": "auto",
    },
    {
        "id": "08",
        "name": "学生住宿管理规定",
        "title": "武昌理工学院学生住宿管理规定",
        "url": "https://xgc.wut.edu.cn/info/1771/6011.htm",
        "category": "宿舍管理",
        "mode": "auto",
    },
    {
        "id": "09",
        "name": "学生请假暂行规定",
        "title": "武昌理工学院学生请假暂行规定",
        "url": "https://nurse.wut.edu.cn/info/1651/7831.htm",
        "category": "考勤请假",
        "mode": "auto",
    },
    {
        "id": "10",
        "name": "奖助学金评定办法",
        "title": "武昌理工学院国家奖学金、国家励志奖学金、国家助学金评定办法",
        "url": "https://nurse.wut.edu.cn/info/1651/7731.htm",
        "category": "奖助学金",
        "mode": "auto",
    },
    {
        "id": "11",
        "name": "国家助学金实施办法",
        "title": "武昌理工学院国家助学金实施办法",
        "url": "https://art.wut.edu.cn/info/1761/47751.htm",
        "category": "奖助学金",
        "mode": "auto",
        "note": "与 10 号文件条款有重叠，金额标准以最新版为准",
    },
    {
        "id": "12",
        "name": "图书馆管理制度",
        "title": "武昌理工学院图书馆管理制度",
        "url": "https://tw.wut.edu.cn/info/1251/53541.htm",
        "category": "图书馆",
        "mode": "auto",
    },
    {
        "id": "13",
        "name": "图书馆入馆规定",
        "title": "武昌理工学院图书馆入馆规定",
        "url": "https://znlib.wut.edu.cn/bggk/rggd.htm",
        "category": "图书馆",
        "mode": "auto",
    },
    {
        "id": "14",
        "name": "图书馆常见问题",
        "title": "武昌理工学院图书馆常见问题",
        "url": "https://znlib.wut.edu.cn/lxwm/FAQ.htm",
        "category": "图书馆",
        "mode": "table",
    },
    {
        "id": "15",
        "name": "平时成绩管理办法",
        "title": "武昌理工学院智能建造学院平时成绩管理办法",
        "url": "https://city.wut.edu.cn/info/1361/35041.htm",
        "category": "教学管理",
        "mode": "auto",
        "note": "学院级文件，缺勤取消考试资格等规定对全校有参考价值",
    },
    {
        "id": "16",
        "name": "招生章程",
        "title": "武昌理工学院2024年招生章程",
        "url": "https://xxgk.wut.edu.cn/info/1301/7984.htm",
        "category": "招生",
        "mode": "auto",
    },
    {
        "id": "17",
        "name": "学生重评缓评暂行规定",
        "title": "武昌理工学院学生重评、缓评暂行规定",
        "url": "https://cie.wut.edu.cn/info/1851/13551.htm",
        "category": "考试管理",
        "mode": "auto",
    },
    {
        "id": "18",
        "name": "学生宿舍文明建设管理规定",
        "title": "武昌理工学院学生宿舍文明建设管理规定",
        "url": "https://xgc.wut.edu.cn/info/1771/34671.htm",
        "category": "宿舍管理",
        "mode": "auto",
        "note": "含熄灯 23:00、晚归认定与处分标准",
    },
    {
        "id": "19",
        "name": "学生公寓门禁管理暂行规定",
        "title": "武昌理工学院学生公寓门禁管理暂行规定",
        "url": "https://xgc.wut.edu.cn/info/1771/6031.htm",
        "category": "宿舍管理",
        "mode": "auto",
        "note": "校园卡门禁、遗失补办时限",
    },
    {
        "id": "20",
        "name": "校园一卡通使用指南",
        "title": "武昌理工学院校园一卡通使用指南",
        "url": "https://ecard.wut.edu.cn/info/1221/3752.htm",
        "category": "校园卡",
        "mode": "auto",
        "note": "含挂失、补办、充值、宿舍购电流程",
    },
    {
        "id": "21",
        "name": "新生入学指南学校住宿",
        "title": "武昌理工学院新生入学指南·学校住宿",
        "url": "https://www.wut.edu.cn/info/2711/169021.htm",
        "category": "宿舍管理",
        "mode": "auto",
        "note": "含作息（6:00 开门、23:00 关门熄灯、周五周六不熄灯、五一至十一通宵供电）与住宿费标准；原始页面为 2020 年版，时效性需注意",
        "start_marker": "学生公寓共有22栋",
    },
]

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
ARTICLE_RE = re.compile(r"^第\s*([一二三四五六七八九十百零〇0-9]{1,5})\s*条\s*(.*)$")
CHAPTER_RE = re.compile(r"^第\s*([一二三四五六七八九十百零〇0-9]{1,5})\s*章\s*(.*)$")
SECTION_RE = re.compile(r"^第\s*([一二三四五六七八九十百零〇0-9]{1,5})\s*节\s*(.*)$")
CN_ITEM_RE = re.compile(r"^([一二三四五六七八九十]{1,3})、\s*(.*)$")
NUM_ITEM_RE = re.compile(r"^(\d{1,3})\s*[.、]\s*(.*)$")

# 页面元信息：出现在正文首尾，不属于校规内容
META_RE = re.compile(
    r"(校对：|责编：|终审：|审核：|编辑：|时间：\s*20|阅读：|_showDynClicks"
    r"|上一篇|下一篇|打印本页|关闭窗口|点击次数|字体：|分享到|扫一扫)"
)
# 纯标点碎片：被 <span> 拆开的 "(" ")" "、" 等
PUNCT_ONLY_RE = re.compile(r"^[（）()【】\[\]〔〕、，。；：·—\-…\s]{1,3}$")

# 一行里挤了多个一级编号（官网用 &nbsp; 分隔）
INLINE_CN_ITEM_RE = re.compile(r"(?<=[。：；！？\s])([一二三四五六七八九十]{1,2}、)")
INLINE_NUM_ITEM_RE = re.compile(r"(?<=[。；：\s])(\d{1,2}[.、])(?=\D)")

# 行内小标题：`作息时间：学生公寓楼大门早6:00开门……`
# 口语体裁（新生指南、服务指南）常用这种写法，不拆开就会整篇变成一个 chunk。
# 必须排除括号开头——"（一）纪律处分：包括警告……"是条款内的枚举项，
# 拆成章节会把一条规定切碎，并丢掉它与主条款的归属关系。
INLINE_HEADING_RE = re.compile(r"^([^：:，。；！？（）()\s,.;]{2,12})[：:]\s*(.{12,})$")
BARE_HEADING_RE = re.compile(r"^([^：:，。；！？（）()\s,.;]{2,10})[：:]$")


@dataclass
class Doc:
    """一篇文档的抽取结果。"""

    source: dict[str, str]
    lines: list[str] = field(default_factory=list)
    table_rows: list[tuple[str, str]] = field(default_factory=list)
    raw_chars: int = 0
    container: str = ""


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.md5(url.encode()).hexdigest()[:12] + ".html")


def fetch(url: str, cache_dir: Path, force: bool = False) -> str:
    path = cache_path(cache_dir, url)
    if path.exists() and not force and path.stat().st_size > 1000:
        return path.read_text(encoding="utf-8", errors="ignore")
    cache_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-A", USER_AGENT, url, "-o", str(path)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not path.exists() or path.stat().st_size < 1000:
        raise RuntimeError(f"下载失败：{url}")
    return path.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------
def _balanced_slice(raw: str, open_tag_match: re.Match[str], tag: str = "div") -> str:
    """从已匹配的开标签位置起，按标签配平截取到对应的闭标签。

    正则 `(.*?)</div>` 会在第一个嵌套闭标签处截断——官网正文里普遍有嵌套 div，
    用非贪婪正则会丢掉后半篇内容（实测 41 号令只剩 60%）。这里改为逐标签计数。
    """
    start = open_tag_match.end()
    depth = 1
    pos = start
    tag_re = re.compile(rf"(?is)<(/?){tag}\b[^>]*>")
    while depth > 0:
        token = tag_re.search(raw, pos)
        if token is None:
            return raw[start:]
        depth += -1 if token.group(1) == "/" else 1
        if depth == 0:
            return raw[start : token.start()]
        pos = token.end()
    return raw[start:]


def _find_container(raw: str, class_keyword: str, tag: str = "div") -> str | None:
    pattern = re.compile(
        rf'(?is)<{tag}\b[^>]*class="[^"]*{re.escape(class_keyword)}[^"]*"[^>]*>'
    )
    match = pattern.search(raw)
    if match is None:
        return None
    return _balanced_slice(raw, match, tag)


CONTAINER_KEYS = ["v_news_content", "wp_articlecontent", "article", "content", "TRS_Editor"]
CONTAINER_TAGS = ["div", "article", "td"]


def html_to_text(fragment: str) -> str:
    """HTML 片段 → 纯文本（块级标签视为换行）。"""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", "", fragment)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|table|section|article)>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    # 双重反转义：部分页面把 &nbsp; 存成 &amp;nbsp;
    text = html_lib.unescape(html_lib.unescape(text))
    return text.replace("\u3000", " ").replace("\xa0", " ")


def extract_table(fragment: str) -> list[tuple[str, str]]:
    """从问答表格里抽 (问, 答) 对。"""
    rows: list[tuple[str, str]] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", fragment):
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)
        if len(cells) < 2:
            continue
        question = re.sub(r"\s+", " ", html_to_text(cells[0])).strip()
        answer = re.sub(r"\s+", " ", html_to_text(cells[1])).strip()
        if not question or not answer:
            continue
        if question in {"提问", "问题"} and answer in {"回答", "解答"}:
            continue  # 表头
        if question == answer:
            continue
        rows.append((question, answer))
    return rows


def extract(source: dict[str, str], raw: str) -> Doc:
    doc = Doc(source=source)
    for key in CONTAINER_KEYS:
        for tag in CONTAINER_TAGS:
            fragment = _find_container(raw, key, tag)
            if not fragment or len(fragment) < 300:
                continue
            doc.container = f"{tag}.{key}"
            if source.get("mode") == "table":
                doc.table_rows = extract_table(fragment)
                if doc.table_rows:
                    return doc
            text = html_to_text(fragment)
            doc.raw_chars = len(text)
            doc.lines = text.splitlines()
            return doc
    raise RuntimeError(f"未命中正文容器：{source['url']}")


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------
def split_inline_items(line: str) -> list[str]:
    """把被 &nbsp; 挤在一行的多个编号项拆开。

    官网常见：`……特制定本规定：   一、xxx   二、yyy`
    只在编号前面是标点或空白时切，避免误切正文里的"分为一、二、三类"。
    """
    if len(line) < 80:
        return [line]
    return [seg.strip() for seg in _split_by_items(line) if seg.strip()]


def _split_by_items(line: str) -> list[str]:
    positions = [m.start() for m in INLINE_CN_ITEM_RE.finditer(line)]
    if len(positions) < 2:
        return [line]
    segments: list[str] = []
    last = 0
    for pos in positions:
        if pos > last:
            segments.append(line[last:pos])
        last = pos
    segments.append(line[last:])
    return segments


def clean_lines(doc: Doc) -> list[str]:
    lines: list[str] = []
    for raw_line in doc.lines:
        line = re.sub(r"[ \t\r\f\v]+", " ", raw_line).strip()
        if not line or not line.strip(" "):
            continue
        if META_RE.search(line):
            continue
        for piece in split_inline_items(line):
            piece = piece.strip()
            if piece:
                lines.append(piece)

    # 合并被 <span> 拆散的标点碎片：单独成行的 "(" "）" 贴回相邻行
    merged: list[str] = []
    for line in lines:
        if PUNCT_ONLY_RE.match(line) and merged:
            merged[-1] = merged[-1] + line
        else:
            merged.append(line)
    lines = merged

    # 条款号独占一行时，与下一行合并（官网把 "第二十五条" 单独放在 <p> 里）
    combined: list[str] = []
    pending: str | None = None
    for line in lines:
        if pending is not None:
            line = f"{pending} {line}".strip()
            pending = None
        match = ARTICLE_RE.match(line)
        if match and not match.group(2).strip():
            pending = f"第{match.group(1)}条"
            continue
        combined.append(line)
    if pending is not None:
        combined.append(pending)

    # 可选：从指定标记开始收录（新闻/指南体裁常有与主题无关的铺垫段落）
    marker = doc.source.get("start_marker")
    if marker:
        for index, line in enumerate(combined):
            if marker in line:
                combined = combined[index:]
                break
    return combined


def _similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio()


def normalize_structure(lines: list[str], title: str) -> list[str]:
    """规整章节/条款格式，去重复标题，把孤立短标题提升为章节。"""
    out: list[str] = []
    for line in lines:
        chapter = CHAPTER_RE.match(line)
        if chapter:
            body = chapter.group(2).strip()
            out.append(f"第{chapter.group(1)}章 {body}".strip())
            continue
        section = SECTION_RE.match(line)
        if section:
            body = section.group(2).strip()
            out.append(f"第{section.group(1)}节 {body}".strip())
            continue
        article = ARTICLE_RE.match(line)
        if article:
            body = article.group(2).strip()
            out.append(f"第{article.group(1)}条 {body}".strip())
            continue
        # 正文里重复出现的大标题：与配置标题高度相似即视为重复
        # （官网常把标题写进正文首行，且与页面标题差一两个字，如"学籍管理规定"与"学生学籍管理规定"）
        if len(line) >= 8 and _similarity(line, title) > 0.82:
            continue
        # 行内小标题（口语体裁）：拆成"章节 + 内容"，否则整篇会挤成一个 chunk
        inline_heading = INLINE_HEADING_RE.match(line)
        if inline_heading:
            out.append(f"## {inline_heading.group(1).strip()}")
            out.append(inline_heading.group(2).strip())
            continue
        bare_heading = BARE_HEADING_RE.match(line)
        if bare_heading:
            out.append(f"## {bare_heading.group(1).strip()}")
            continue
        out.append(line)

    # 孤立短标题（如"总 则"独占一行、下文直接跟"第一条"）提升为章节
    promoted: list[str] = []
    has_chapter = False
    for index, line in enumerate(out):
        if line.startswith("第") and (CHAPTER_RE.match(line) or SECTION_RE.match(line)):
            has_chapter = True
        if line.startswith("## "):
            has_chapter = True
        nxt = out[index + 1] if index + 1 < len(out) else ""
        looks_like_heading = (
            len(line) <= 12
            and not re.search(r"[，。；：,.;:！？]", line)
            and bool(ARTICLE_RE.match(nxt))
        )
        if looks_like_heading and not has_chapter and not line.startswith("## "):
            promoted.append(f"## {re.sub(r'[ \\u3000]+', '', line)}")
            has_chapter = True
            continue
        promoted.append(line)
    return promoted


def is_section_heading(text: str) -> bool:
    """判断编号项是"小节标题"还是"带内容的条款"。

    标题：短、不含句读；条款：成句、带逗号句号。
    """
    body = text.strip()
    if not body:
        return False
    if len(body) > 22:
        return False
    return not re.search(r"[，。；：]", body)


# ---------------------------------------------------------------------------
# 生成 Markdown
# ---------------------------------------------------------------------------
def _drop_meta_preface(lines: list[str]) -> list[str]:
    """丢弃正文开头纯元信息（文号、日期）造成的空壳 chunk。

    只在"结构标记之前、至多 3 行、每行都很短"时才丢，
    避免把真正的前言（如"为加强……特制定本规定"）也删掉。
    """
    start = 0
    for index, line in enumerate(lines):
        if line.startswith("## ") or CHAPTER_RE.match(line) or SECTION_RE.match(line) or ARTICLE_RE.match(line):
            start = index
            break
    else:
        return lines
    preface = lines[:start]
    if preface and len(preface) <= 3 and all(len(item) <= 30 for item in preface):
        return lines[start:]
    return lines


def render(doc: Doc) -> tuple[str, dict[str, int]]:
    source = doc.source
    title = source["title"]
    lines = _drop_meta_preface(normalize_structure(clean_lines(doc), title))
    out: list[str] = [f"# {title}", ""]
    stats = {"chapter": 0, "article": 0, "chunks_hint": 0}

    def emit(line: str = "") -> None:
        """写入一行，避免连续空行。"""
        if line == "" and out and out[-1] == "":
            return
        out.append(line)

    if source.get("mode") == "table" and doc.table_rows:
        emit("## 常见问题")
        emit("")
        for index, (question, answer) in enumerate(doc.table_rows, start=1):
            emit(f"第{index}条 问：{question}")
            emit(f"答：{answer}")
            emit("")
            stats["article"] += 1
        stats["chapter"] = 1
        stats["chunks_hint"] = stats["article"]
        return "\n".join(out).strip() + "\n", stats

    has_article = any(ARTICLE_RE.match(line) for line in lines)
    auto_no = 0

    for line in lines:
        if line.startswith("## "):
            emit("")
            emit(line)
            emit("")
            stats["chapter"] += 1
            continue
        chapter = CHAPTER_RE.match(line)
        if chapter:
            emit("")
            emit(f"## {line}")
            emit("")
            stats["chapter"] += 1
            continue
        section = SECTION_RE.match(line)
        if section:
            emit("")
            emit(f"## {line}")
            emit("")
            stats["chapter"] += 1
            continue
        if ARTICLE_RE.match(line):
            emit("")
            emit(line)
            stats["article"] += 1
            continue
        if not has_article:
            cn = CN_ITEM_RE.match(line)
            if cn:
                body = cn.group(2).strip()
                if is_section_heading(body):
                    emit("")
                    emit(f"## {line}")
                    emit("")
                    stats["chapter"] += 1
                else:
                    auto_no += 1
                    emit("")
                    emit(f"第{auto_no}条 {line}")
                    stats["article"] += 1
                continue
            num = NUM_ITEM_RE.match(line)
            if num:
                auto_no += 1
                emit("")
                emit(f"第{auto_no}条 {line}")
                stats["article"] += 1
                continue
        emit(line)

    stats["chunks_hint"] = max(stats["article"], 1)
    return "\n".join(out).strip() + "\n", stats


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="抓取并清洗武昌理工学院校规")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE), help="HTML 缓存目录")
    parser.add_argument("--force", action="store_true", help="忽略缓存重新下载")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    parser.add_argument("--only", default="", help="只处理指定编号，逗号分隔，如 01,07")
    args = parser.parse_args()

    out_dir = Path(args.out)
    cache_dir = Path(args.cache)
    wanted = {item.strip() for item in args.only.split(",") if item.strip()}
    selected = [item for item in SOURCES if not wanted or item["id"] in wanted]

    results: list[dict[str, object]] = []
    failures: list[str] = []

    print(f"{'#':3s} {'篇名':26s} {'容器':15s} {'章':>3s} {'条':>3s} {'字符':>6s}")
    print("-" * 74)

    for source in selected:
        try:
            raw = fetch(source["url"], cache_dir, args.force)
            doc = extract(source, raw)
            content, stats = render(doc)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{source['id']} {source['name']}：{exc}")
            print(f"{source['id']:3s} {source['name']:26s} !! {exc}")
            continue

        file_name = f"{source['id']}-{source['name']}.md"
        if not args.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / file_name).write_text(content, encoding="utf-8")

        chars = len(content)
        results.append(
            {
                "id": source["id"],
                "file": file_name,
                "title": source["title"],
                "url": source["url"],
                "category": source["category"],
                "note": source.get("note", ""),
                "chapters": stats["chapter"],
                "articles": stats["article"],
                "chars": chars,
            }
        )
        print(
            f"{source['id']:3s} {source['name']:26s} {doc.container:15s} "
            f"{stats['chapter']:3d} {stats['article']:3d} {chars:6d}"
        )

    print("-" * 74)
    total_articles = sum(int(item["articles"]) for item in results)
    total_chars = sum(int(item["chars"]) for item in results)
    print(f"共 {len(results)} 篇，{total_articles} 条，{total_chars} 字符")

    if results and not args.dry_run:
        manifest = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "docs": results,
        }
        (out_dir / "_sources.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"来源清单：{out_dir / '_sources.json'}")

    if failures:
        print("\n失败：")
        for item in failures:
            print(" -", item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
