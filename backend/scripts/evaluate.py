"""离线评测脚本：召回 / 引用 / 拒答 / 时延。

用法（在 backend/ 目录下执行）：

    python scripts/evaluate.py                    # 只跑检索评测（不调用大模型，零成本）
    python scripts/evaluate.py --with-llm         # 额外跑端到端（会调用 DeepSeek，产生费用）
    python scripts/evaluate.py --set tests/golden/qa_set.json --md docs/eval-report.md
    python scripts/evaluate.py --only both,vector # 只跑指定融合配置

输出的指标（论文里可直接用）：

- recall@k：正确条款是否出现在前 k 条证据里（k 默认 3）
- MRR：正确条款排名的倒数均值（0~1，越大越好）
- graph_empty_rate：图谱一路完全没召回的比例（用来解释"双路"到底有没有用）
- refusal_acc：应拒答的拒答了、应回答的回答了，占比
- citation_rate：给出的答案里通过引用校验的比例（only verified 计分子）
- p50 / p95 时延

注意：若 VECTOR_MODE=mock，脚本会打印警告——mock 语料只有 7 条且关键词是人工串好的，
此时的 recall/MRR 只能证明链路通，不能写进论文结论。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.events import NullBus  # noqa: E402
from app.agents.graph import build_graph  # noqa: E402
from app.agents import nodes  # noqa: E402
from app.agents.runtime import NodeContext, WorkflowEngine  # noqa: E402
from app.agents.state import AgentState  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.persistence import MockPersistence  # noqa: E402

DEFAULT_SET = BACKEND_DIR / "tests" / "golden" / "qa_set.json"


def load_set(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["items"]


def _hit(items, expect: str) -> int | None:
    """返回正确条款在证据列表里的名次（从 1 开始），未命中返回 None。"""
    if not expect:
        return None
    for rank, item in enumerate(items, start=1):
        text = f"{item.detail or ''} {item.snippet or ''}"
        if expect in text:
            return rank
    return None


async def collect(services, settings: Settings, question: str, mode: str):
    """按融合配置取证据：vector / graph / both（both 走生产同款 RRF）。"""
    vector_hits = []
    graph_paths = []
    if mode in {"vector", "both"}:
        vector_hits = await services.vector.search(question, top_k=3)
    if mode in {"graph", "both"}:
        graph_paths = await services.graph.search_paths(question, top_k=3)
    if mode == "both":
        state = AgentState(query=question, vector_hits=vector_hits, graph_paths=graph_paths)
        ctx = NodeContext(services=services, settings=settings, bus=NullBus())
        fused = await nodes.fuse_evidence(state, ctx)
        return fused["evidence"], len(vector_hits), len(graph_paths)
    return [*vector_hits, *graph_paths], len(vector_hits), len(graph_paths)


async def run_retrieval(services, settings, items, modes) -> dict:
    results = {}
    for mode in modes:
        ranks: list[int | None] = []
        graph_empty = 0
        answerable = [item for item in items if item["answerable"]]
        for item in answerable:
            evidence, vector_n, graph_n = await collect(services, settings, item["question"], mode)
            ranks.append(_hit(evidence, item["expect"]))
            if mode in {"graph", "both"} and graph_n == 0:
                graph_empty += 1
        hits = sum(1 for rank in ranks if rank is not None)
        mrr = sum((1.0 / rank) for rank in ranks if rank) / max(1, len(ranks))
        results[mode] = {
            "recall": hits / max(1, len(answerable)),
            "mrr": mrr,
            "hits": hits,
            "total": len(answerable),
            "graph_empty_rate": graph_empty / max(1, len(answerable)),
        }
    return results


async def run_e2e(services, settings, items) -> dict:
    spec, entry = build_graph(settings)
    latencies: list[int] = []
    refusal_ok = 0
    cited_ok = 0
    answered = 0
    details = []
    for item in items:
        services.cache.clear()  # 关掉缓存命中，测真实链路
        engine = WorkflowEngine(spec, entry, services, settings)
        started = time.perf_counter()
        state = await engine.run(AgentState(query=item["question"], user_id=9001))
        elapsed = int((time.perf_counter() - started) * 1000)
        latencies.append(elapsed)
        expected_refuse = not item["answerable"]
        if state.refused == expected_refuse:
            refusal_ok += 1
        if not state.refused:
            answered += 1
            if state.grounding_status == "verified":
                cited_ok += 1
        details.append(
            {
                "id": item["id"],
                "question": item["question"],
                "refused": state.refused,
                "grounding_status": state.grounding_status,
                "latency_ms": elapsed,
                "answer_head": (state.answer or "")[:80],
            }
        )
    latencies.sort()
    return {
        "refusal_acc": refusal_ok / max(1, len(items)),
        "citation_rate": cited_ok / max(1, answered),
        "answered": answered,
        "p50_ms": statistics.median(latencies) if latencies else 0,
        "p95_ms": latencies[int(len(latencies) * 0.95) - 1] if latencies else 0,
        "details": details,
    }


def format_markdown(settings: Settings, retrieval: dict, e2e: dict | None, count: int) -> str:
    lines = [
        "# 检索与生成评测报告",
        "",
        f"- 语料模式：vector=`{settings.vector_mode}` / graph=`{settings.graph_mode}` / llm=`{settings.llm_mode}`",
        f"- 题量：{count}",
        f"- 证据阈值：{settings.evidence_threshold}；融合模式={settings.fusion_mode}；RRF k={settings.rrf_k}",
        f"- 图谱闸门：准入分 {settings.graph_quality_min_score}；条目占比上限 {settings.graph_max_share}",
        "",
        "## 检索指标",
        "",
        "| 融合配置 | recall@3 | MRR | 命中/总数 | 图谱空召回率 |",
        "| --- | --- | --- | --- | --- |",
    ]
    both_label = f"双路（{settings.fusion_mode}）"
    label = {"vector": "仅向量", "graph": "仅图谱", "both": both_label}
    for mode, row in retrieval.items():
        lines.append(
            f"| {label.get(mode, mode)} | {row['recall']:.3f} | {row['mrr']:.3f} | "
            f"{row['hits']}/{row['total']} | {row['graph_empty_rate']:.3f} |"
        )
    if e2e:
        lines += [
            "",
            "## 端到端指标",
            "",
            "| 拒答准确率 | 引用通过率 | 实际作答数 | p50 时延 | p95 时延 |",
            "| --- | --- | --- | --- | --- |",
            f"| {e2e['refusal_acc']:.3f} | {e2e['citation_rate']:.3f} | {e2e['answered']} | "
            f"{e2e['p50_ms']} ms | {e2e['p95_ms']} ms |",
        ]
    if settings.vector_mode == "mock":
        lines += [
            "",
            "> ⚠️ 当前是 mock 语料（7 条人工关键词），上面的 recall/MRR 只能证明链路通，**不可作为论文实验结论**。",
            "> 请配置 `VECTOR_MODE=qdrant`、`GRAPH_MODE=neo4j` 并完成 `scripts/ingest_rules.py` 入库后重跑。",
        ]
    return "\n".join(lines) + "\n"


async def main_async(args) -> int:
    settings = Settings.from_env()
    if args.fusion:
        settings.fusion_mode = args.fusion
    services = build_services(settings)
    if args.no_persist:
        # 评测不写生产库：避免把测试问答灌进会话表
        services.persistence = MockPersistence()
    items = load_set(Path(args.set))
    if args.limit:
        items = items[: args.limit]
    modes = [mode.strip() for mode in args.only.split(",") if mode.strip()]

    retrieval = await run_retrieval(services, settings, items, modes)
    e2e = await run_e2e(services, settings, items) if args.with_llm else None
    if e2e and args.verbose:
        expected_map = {item["id"]: item["answerable"] for item in items}
        print("\n=== 逐题明细 ===")
        print(f"{'题号':5s} {'应拒答':6s} {'实际':5s} {'引用':10s} {'时延':>7s}  问题")
        for row in e2e["details"]:
            expect_refuse = not expected_map[row["id"]]
            flag = "OK " if row["refused"] == expect_refuse else "!! "
            print(
                f"{flag}{row['id']:4s} {str(expect_refuse):6s} {str(row['refused']):5s} "
                f"{row['grounding_status']:10s} {row['latency_ms']:6d}ms  {row['question'][:26]}"
            )

    report = format_markdown(settings, retrieval, e2e, len(items))
    print(report)
    if args.md:
        target = Path(args.md)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
        print(f"[eval] Markdown 报告已写入 {target}")
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"retrieval": retrieval, "e2e": e2e}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[eval] JSON 结果已写入 {target}")

    for service in (services.graph, services.vector):
        close = getattr(service, "close", None)
        if close:
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校规问答系统离线评测")
    parser.add_argument("--set", default=str(DEFAULT_SET), help="金标集 JSON 路径")
    parser.add_argument("--only", default="vector,graph,both", help="融合配置，逗号分隔")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--with-llm", action="store_true", help="额外跑端到端（会调用大模型）")
    parser.add_argument(
        "--fusion", default="", choices=["", "append", "gated", "rrf"], help="覆盖融合策略，用于消融对比"
    )
    parser.add_argument("--no-persist", action="store_true", default=True, help="不写生产库（默认开启）")
    parser.add_argument("--md", default="", help="Markdown 报告输出路径")
    parser.add_argument("--json", default="", help="JSON 结果输出路径")
    parser.add_argument("--verbose", action="store_true", help="打印逐题明细（拒答/引用/时延）")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
