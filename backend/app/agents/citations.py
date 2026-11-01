"""引用校验节点：三态判定 + 条款号核对。

规则：答案里出现的每个条款号都必须能在召回证据里找到；编造条款号直接判不通过。
做不到 claim 级蕴含（需要 NLI 模型），因此对外表述为"引用可核对性校验"。
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.runtime import NodeContext
from app.agents.state import AgentState
from app.schemas.chat import Evidence

ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条")

GRAPH_TITLE_PREFIX = "知识图谱 · "

def _clean_title(title: str) -> str:
    title = (title or "").strip()
    if title.startswith(GRAPH_TITLE_PREFIX):
        title = title[len(GRAPH_TITLE_PREFIX):]
    return title.strip()

def _collect_evidence_citations(evidence: list[Evidence]) -> tuple[set[str], set[str]]:
    """从召回证据里抽出可核对的两类标识：文档名、条款号。"""
    titles: set[str] = set()
    articles: set[str] = set()
    for item in evidence:
        title = _clean_title(item.title)
        if title:
            titles.add(title)
        articles.update(ARTICLE_RE.findall(item.detail or ""))
    if not articles:
        # 详情里没有条款号时（工具证据/公告），退回从原文里抽，避免一律判失败
        for item in evidence:
            articles.update(ARTICLE_RE.findall(item.snippet or ""))
    return titles, articles

async def verify_grounding(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    """引用校验（三态）。

    判定规则（比旧版严格）：
    1. 答案里出现的**每一个**条款号都必须能在召回证据里找到 —— 编造"第99条"直接不通过；
    2. 至少要引用一个条款号或一个文档名 —— 通篇不带依据也不通过；
    3. 答案长度下限 10 字。

    仍然做不到"claim 级蕴含判断"（那需要 NLI 模型），但已经堵住最典型的两种
    幻觉：编造条款号、完全不引用。论文里应如实描述为"引用可核对性校验"。
    """
    answer = state.answer or ""
    titles, articles = _collect_evidence_citations(state.evidence)
    cited = set(ARTICLE_RE.findall(answer))
    fabricated = sorted(cited - articles)
    title_hit = any(title in answer for title in titles if title)
    unsupported = sorted(cited) if not articles and cited else fabricated

    if len(answer.strip()) < 10:
        return {
            "grounding_status": "unverified",
            "grounding_reason": "答案过短，无法核对引用",
            "regen_count": state.regen_count + 1,
        }
    if unsupported:
        return {
            "grounding_status": "unverified",
            "grounding_reason": "引用了未召回的条款：" + "、".join(unsupported[:3]),
            "regen_count": state.regen_count + 1,
        }
    if not cited and not title_hit:
        return {
            "grounding_status": "unverified",
            "grounding_reason": "未引用任何条款号或文档名",
            "regen_count": state.regen_count + 1,
        }
    return {
        "grounding_status": "verified",
        "grounding_reason": (
            f"引用条款 {len(cited)} 处，均在召回集内" if cited else "引用了召回的文档名"
        ),
    }
