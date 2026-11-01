"""证据融合节点：RRF 排序 + 图谱质量闸门 + 图谱占比上限。

独立成文件的原因：这段是全链路里最需要按实验数据调参的部分
（RRF_K / GRAPH_QUALITY_MIN_SCORE / GRAPH_MAX_SHARE），
和"图上有哪些节点"是两件事。
"""

from __future__ import annotations

from typing import Any

from app.agents.runtime import NodeContext
from app.agents.state import AgentState
from app.schemas.chat import Evidence, SourceItem

def _evidence_key(item: Evidence) -> tuple[str, str, str]:
    return (item.type, item.title, item.detail)

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
    score = max((item.score for item in merged), default=0.0)
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
