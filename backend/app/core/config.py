"""配置：统一从 backend/.env 与环境变量读取，后续接三库时只改这里。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# backend/ 目录（config.py 在 backend/app/core/ 下）
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    """运行配置。

    vector_mode / graph_mode / llm_mode 是整个平台的关键开关：
    现场演示时任何外部依赖不可用，都可以切回 mock，保证答辩不翻车。
    """

    app_name: str = "校纪校规问答平台"
    version: str = "0.2.0-course"

    # ---- 存储 / 模型开关 ----
    vector_mode: str = "mock"    # mock | qdrant（qdrant 支持本地嵌入式模式）
    graph_mode: str = "mock"     # mock | neo4j
    llm_mode: str = "deepseek"   # deepseek | fake

    # ---- DeepSeek ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    llm_timeout: float = 60.0

    # ---- 编排参数（图引擎的循环/步数上限）----
    max_steps: int = 24
    max_retrieval_rounds: int = 2
    max_regen: int = 1
    evidence_threshold: float = 0.5
    node_timeout: float = 120.0

    # ---- 真实存储（Day2 起使用）----
    mysql_url: str = ""
    qdrant_path: str = ""
    qdrant_url: str = ""
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            vector_mode=os.getenv("VECTOR_MODE", "mock").strip().lower(),
            graph_mode=os.getenv("GRAPH_MODE", "mock").strip().lower(),
            llm_mode=os.getenv("LLM_MODE", "deepseek").strip().lower(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            llm_temperature=_env_float("LLM_TEMPERATURE", 0.2),
            llm_timeout=_env_float("LLM_TIMEOUT_SECONDS", 60.0),
            max_steps=_env_int("MAX_STEPS", 24),
            max_retrieval_rounds=_env_int("MAX_RETRIEVAL_ROUNDS", 2),
            max_regen=_env_int("MAX_REGEN", 1),
            evidence_threshold=_env_float("EVIDENCE_THRESHOLD", 0.5),
            mysql_url=os.getenv("MYSQL_URL", "").strip(),
            qdrant_path=os.getenv("QDRANT_PATH", "").strip(),
            qdrant_url=os.getenv("QDRANT_URL", "").strip(),
            neo4j_uri=os.getenv("NEO4J_URI", "").strip(),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j").strip(),
            neo4j_password=os.getenv("NEO4J_PASSWORD", "").strip(),
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.deepseek_api_key) or self.llm_mode == "fake"
