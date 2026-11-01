"""证据融合节点：RRF 排序 + 图谱质量闸门 + 图谱占比上限。

独立成文件的原因：这段是全链路里最需要按实验数据调参的部分
（RRF_K / GRAPH_QUALITY_MIN_SCORE / GRAPH_MAX_SHARE），
和"图上有哪些节点"是两件事。
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.runtime import NodeContext
from app.agents.state import AgentState
from app.schemas.chat import Evidence, SourceItem

def _evidence_key(item: Evidence) -> tuple[str, str, str]:
    """去重键：优先按条款正文，其次按 (type, title, detail)。

    图谱证据的标题带"知识图谱 · "前缀、type 也不同，只靠元组无法识别
    "同一条款被向量和图谱各召回一次"，会导致同一段原文重复进入 prompt。
    """
    text = re.sub(r"\s+", "", item.snippet or "")
    if text:
        return ("", text[:120], "")
    return (item.type, item.title, item.detail)


def _append_merge(
    vector_hits: list[Evidence],
    graph_items: list[Evidence],
    tool_evidence: list[Evidence],
    share: float,
) -> tuple[list[Evidence], int]:
    """追加式融合：向量决定排序，图谱只补充向量没有的条款。

    依据（真实语料 21 篇 / 20 题实测）：
    - 图谱一路单独 recall@3 只有 0.30，是向量（0.80）的 37.5%，但能补到向量漏掉的 1 题；
    - 等权 RRF 把图谱证据排到前面，recall 升到 0.85 但 MRR 从 0.600 掉到 0.454；
    - 改为追加后 recall 0.850、MRR 0.610，两项同时不劣于仅向量。

    结论：图谱在当前语料下只有"补充召回"价值，不具备"排序"价值，
    因此不参与排序竞争，只保证正确条款进入候选集。
    """
    # 工具可能被 LLM 连续调用多次（ReAct 两轮），同一公告会重复出现；
    # 这里按正文去重，避免同一条依据在 sources 里列两遍。
    merged: list[Evidence] = []
    seen: set[tuple[str, str, str]] = set()
    for item in list(tool_evidence) + list(vector_hits):
        key = _evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    cap = max(1, int((len(merged) + len(graph_items)) * share + 0.999)) if share > 0 else 10**6
    added = 0
    for item in graph_items:
        key = _evidence_key(item)
        if key in seen:
            continue
        if added >= cap:
            break
        seen.add(key)
        merged.append(item)
        added += 1
    return merged, added

def _evidence_score(state: AgentState, merged: list[Evidence]) -> float:
    """证据充分性用的分数只看向量 cosine。

    图谱分是按关键词覆盖率标定的代理值（真实语料下普遍落在 0.95），
    若参与取最大值，"有图谱命中就判充分"会让"证据不足→扩展→拒答"链路失效。
    向量为空（纯图谱模式）时才退回图谱分。
    """
    vector_max = max((item.score for item in state.vector_hits), default=0.0)
    if vector_max > 0:
        return vector_max
    return max((item.score for item in merged), default=0.0)


async def fuse_evidence(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """RRF 融合排序 + 分路标定的证据分。

    P1-3 修复：
    - 旧实现按 `item.score` 直接排序，而图谱分是 `0.55 + 0.1*hits` 的硬编码，
      必然高于向量 cosine，导致"只要图谱命中就判证据充分"；
    - 现在排序用 RRF（只看排名，不看原始量纲），充分性判定仍用 0~1 的标定分，
      避免把阈值语义改掉（RRF 原始分只有 0.03 量级，直接比 0.5 会导致全站拒答）。
    """
    k = max(1, int(ctx.settings.rrf_k))

    # ---- 图谱质量闸门 ----
    # 图谱一路的分数是按关键词覆盖率标定的，覆盖率低时分会接近 0.46~0.5。
    # 这种"弱命中"如果照样参与融合，会挤占排序位置、稀释向量召回的好结果
    # （评测里表现为双路 MRR 反而低于仅向量）。因此 gated 模式下：
    # 图谱最高分不到准入线 → 整路不参与，并在 evidence_meta 留痕。
    graph_items = list(state.graph_paths)
    graph_best = max((item.score for item in graph_items), default=0.0)
    graph_gated = False
    if ctx.settings.fusion_mode == "gated" and graph_items:
        if graph_best < ctx.settings.graph_quality_min_score:
            graph_gated = True
            graph_items = []

    if ctx.settings.fusion_mode == "append":
        merged_append, graph_kept = _append_merge(
            state.vector_hits,
            graph_items,
            state.tool_evidence,
            ctx.settings.graph_max_share,
        )
        meta = {
            "fusion_mode": "append",
            "rrf_k": k,
            "rrf_top": 0.0,
            "vector_max": round(max((item.score for item in state.vector_hits), default=0.0), 3),
            "graph_max": round(graph_best, 3),
            "graph_count": len(state.graph_paths),
            "graph_kept": graph_kept,
            "graph_gated": graph_gated,
            "tool_count": len(state.tool_evidence),
        }
        sources = [
            SourceItem(type=item.type, title=item.title, detail=item.detail, snippet=item.snippet)
            for item in merged_append
        ]
        await ctx.emit(
            "meta",
            {"sources": [source.model_dump() for source in sources], "cached": False, "fusion": meta},
        )
        return {
            "evidence": merged_append,
            "evidence_score": _evidence_score(state, merged_append),
            "evidence_meta": meta,
            "sources": sources,
        }

    lists = [state.vector_hits, graph_items, state.tool_evidence]

    rrf: dict[tuple[str, str, str], float] = {}
    merged_map: dict[tuple[str, str, str], Evidence] = {}
    for items in lists:
        seen_in_list: set[tuple[str, str, str]] = set()
        for rank, item in enumerate(items, start=1):
            key = _evidence_key(item)
            if key in seen_in_list:  # 同一路里的重复不计分
                continue
            seen_in_list.add(key)
            rrf[key] = rrf.get(key, 0.0) + 1.0 / (k + rank)
            current = merged_map.get(key)
            if current is None or item.score > current.score:
                merged_map[key] = item

    merged = sorted(
        merged_map.values(),
        key=lambda item: (rrf.get(_evidence_key(item), 0.0), item.score),
        reverse=True,
    )

    # ---- 图谱条目占比上限 ----
    # 即使图谱质量达标，也不允许它占满 prompt：按 graph_max_share 截断，
    # 把位置留给向量召回（条款级召回通常更贴题）。
    graph_kept = sum(1 for item in merged if item.type == "graph")
    if merged and ctx.settings.graph_max_share > 0:
        cap = max(1, int(len(merged) * ctx.settings.graph_max_share + 0.999))
        kept: list[Evidence] = []
        graph_count = 0
        for item in merged:
            if item.type == "graph":
                if graph_count >= cap:
                    continue
                graph_count += 1
            kept.append(item)
        merged = kept
        graph_kept = graph_count

    sources = [
        SourceItem(
            type=item.type,
            title=item.title,
            detail=item.detail,
            snippet=item.snippet,
        )
        for item in merged
    ]
    score = _evidence_score(state, merged)
    meta = {
        "fusion_mode": ctx.settings.fusion_mode,
        "rrf_k": k,
        "rrf_top": round(max(rrf.values(), default=0.0), 4),
        "vector_max": round(max((item.score for item in state.vector_hits), default=0.0), 3),
        "graph_max": round(graph_best, 3),
        "graph_count": len(state.graph_paths),
        "graph_kept": graph_kept,
        "graph_gated": graph_gated,
        "tool_count": len(state.tool_evidence),
    }
    await ctx.emit(
        "meta",
        {"sources": [source.model_dump() for source in sources], "cached": False, "fusion": meta},
    )
    return {
        "evidence": merged,
        "evidence_score": score,
        "evidence_meta": meta,
        "sources": sources,
    }
