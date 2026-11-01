"""命令行：把校规文档灌入 MySQL + Qdrant + Neo4j。

用法（在 backend 目录下）：
    python scripts/ingest_rules.py --reset              # 默认入库真实语料 rules/wut
    python scripts/ingest_rules.py --dir ../rules/_sample --reset   # 链路自测用样例

说明：
- 需要 MySQL 已建库建表（sql/init_mysql.sql）、Neo4j 已启动；
- 首次运行会加载 BGE 模型（约 1~3 分钟）；
- --reset 会清空并重建三个库中的校规数据（不影响问答历史/用户）；
- `rules/_sample/` 是虚构样例，只用于验证链路，**不要用于生产**；
  真实语料由 `scripts/fetch_rules.py` 抓取官网后生成到 `rules/wut/`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services.ingestion import RulesIngestor  # noqa: E402

DEFAULT_MYSQL_URL = "mysql+pymysql://root:@127.0.0.1:3306/school_rules_rag?charset=utf8mb4"


def main() -> int:
    parser = argparse.ArgumentParser(description="校规文档入库（MySQL + Qdrant + Neo4j）")
    parser.add_argument(
        "--dir",
        default=str(BACKEND_DIR.parent / "rules" / "wut"),
        help="校规文档目录（默认真实语料 rules/wut；链路自测可用 rules/_sample）",
    )
    parser.add_argument("--reset", action="store_true", help="清空并重建三库中的校规数据")
    args = parser.parse_args()

    settings = Settings.from_env()
    # 入库脚本强制使用真实存储
    settings.vector_mode = "qdrant"
    settings.graph_mode = "neo4j"
    settings.mysql_url = settings.mysql_url or DEFAULT_MYSQL_URL
    settings.qdrant_path = settings.qdrant_path or "data/qdrant"
    settings.neo4j_uri = settings.neo4j_uri or "bolt://127.0.0.1:7687"

    if not settings.embedding_model_path:
        print("错误：未配置 EMBEDDING_MODEL_PATH（backend/.env 指向本机 bge-small-zh-v1.5 目录）")
        return 1

    print(f"文档目录：{args.dir}")
    print(f"MySQL   ：{settings.mysql_url}")
    print(f"Qdrant  ：{settings.qdrant_path}")
    print(f"Neo4j   ：{settings.neo4j_uri}")
    print("开始解析 + 向量化 + 写图谱（首次需加载 BGE，请稍候）...")

    ingestor = RulesIngestor(settings)
    result = ingestor.run(args.dir, reset=args.reset)
    print(result)
    if result.titles:
        print("已入库文档：" + "、".join(result.titles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
