"""配置：统一从 backend/.env 与环境变量读取，后续接三库时只改这里。

安全默认值约定（P0-1）：
- `auth_required` 默认 True：不配置也必须登录；
- `dev_mode` 默认 False：只有显式开启时才允许匿名，且匿名使用独立的
  `anonymous` 账号，**不再映射到 user_id=1（管理员）**；
- 两者互斥：同时为真属于危险配置，启动即报错，避免"以为开了鉴权其实没开"。
"""

from __future__ import annotations

import os
import secrets
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


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """运行配置。

    vector_mode / graph_mode / llm_mode 是整个平台的关键开关：
    现场演示时任何外部依赖不可用，都可以切回 mock，保证答辩不翻车。
    """

    app_name: str = "校园服务智能问答系统"
    version: str = "0.2.1-course"

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

    # ---- 登录/鉴权 ----
    # auth_required=True（默认）：所有业务接口必须带 Bearer token；
    # dev_mode=True  ：显式放开匿名（仅本地/测试），匿名身份是独立的 anonymous 账号。
    auth_required: bool = True
    dev_mode: bool = False
    allow_registration: bool = True
    anonymous_username: str = "anonymous"
    # 多 worker / 重启后 token 仍有效的关键：必须显式配置 AUTH_TOKEN_SECRET
    token_secret: str = ""
    token_ttl_seconds: int = 8 * 60 * 60

    # ---- 限流 / 配额（P0-3，公网部署必备）----
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 20          # 单主体每分钟请求数
    rate_limit_per_day: int = 300            # 单主体每日问答数（只对 /chat 计数）
    rate_limit_concurrent: int = 1           # 单主体同时进行的问答数

    # ---- 单端口演示：后端直接托管 frontend/dist（需先 npm run build）----
    serve_frontend: bool = False

    # ---- 编排参数（图引擎的循环/步数上限）----
    max_steps: int = 24
    max_retrieval_rounds: int = 2
    max_regen: int = 1
    max_tool_rounds: int = 2
    memory_window: int = 6
    evidence_threshold: float = 0.5
    node_timeout: float = 120.0

    # ---- 检索融合（P1-3 / 质量闸门）----
    # gated：图谱最高分不达标时整路不参与融合（避免弱图谱稀释向量结果）
    # rrf  ：旧行为，两路始终参与，仅做排名融合（消融对照组）
    fusion_mode: str = "gated"
    graph_quality_min_score: float = 0.55    # 图谱一路的准入线（低于则整路剔除）
    graph_max_share: float = 0.5             # 最终证据中图谱条目占比上限
    rrf_k: int = 60                          # RRF 平滑常数
    graph_min_score: float = 0.30            # 图谱证据的最低基础分
    graph_max_score: float = 0.95            # 图谱证据的满分
    graph_keyword_min_coverage: float = 0.34 # 关键词覆盖率低于此值不返回（≈至少 1/3 关键词）

    # ---- 缓存（P1-8）----
    cache_ttl_seconds: int = 30 * 60
    cache_max_entries: int = 1000

    # ---- 工具副作用（P1-7）----
    ticket_idempotency_seconds: int = 600    # 同标题/描述窗口内复用同一工单
    ticket_require_intent: bool = True       # 用户没表达投诉/报修意图时不直接建单

    # ---- 真实存储（Day2 起使用）----
    mysql_url: str = ""
    qdrant_path: str = ""
    qdrant_url: str = ""
    qdrant_collection: str = "rules_chunks"
    embedding_model_path: str = ""
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    def __post_init__(self) -> None:
        # 危险配置组合：以为"开了鉴权 + 开了 dev"，实际会把匿名放进来
        if self.auth_required and self.dev_mode:
            raise ValueError(
                "配置冲突：AUTH_REQUIRED=1 与 DEV_MODE=1 不能同时开启。"
                "本地调试请只设 DEV_MODE=1；部署请只设 AUTH_REQUIRED=1。"
            )
        # 关掉鉴权又不开 dev_mode 属于第三种含糊状态，直接拒绝，避免静默放行
        if not self.auth_required and not self.dev_mode:
            raise ValueError(
                "配置冲突：AUTH_REQUIRED=0 必须同时设置 DEV_MODE=1（仅限本地/测试）。"
                "匿名访问会以独立的 anonymous 身份记录，不再复用管理员账号。"
            )

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
            auth_required=_env_flag("AUTH_REQUIRED", True),
            dev_mode=_env_flag("DEV_MODE", False),
            allow_registration=_env_flag("ALLOW_REGISTRATION", True),
            anonymous_username=os.getenv("ANONYMOUS_USERNAME", "anonymous").strip() or "anonymous",
            token_secret=os.getenv("AUTH_TOKEN_SECRET", "").strip(),
            token_ttl_seconds=_env_int("AUTH_TOKEN_TTL", 8 * 60 * 60),
            rate_limit_enabled=_env_flag("RATE_LIMIT_ENABLED", True),
            rate_limit_per_minute=_env_int("RATE_LIMIT_PER_MINUTE", 20),
            rate_limit_per_day=_env_int("RATE_LIMIT_PER_DAY", 300),
            rate_limit_concurrent=_env_int("RATE_LIMIT_CONCURRENT", 1),
            serve_frontend=_env_flag("SERVE_FRONTEND", False),
            max_steps=_env_int("MAX_STEPS", 24),
            max_retrieval_rounds=_env_int("MAX_RETRIEVAL_ROUNDS", 2),
            max_regen=_env_int("MAX_REGEN", 1),
            max_tool_rounds=_env_int("MAX_TOOL_ROUNDS", 2),
            memory_window=_env_int("MEMORY_WINDOW", 6),
            evidence_threshold=_env_float("EVIDENCE_THRESHOLD", 0.5),
            fusion_mode=os.getenv("FUSION_MODE", "gated").strip().lower(),
            graph_quality_min_score=_env_float("GRAPH_QUALITY_MIN_SCORE", 0.55),
            graph_max_share=_env_float("GRAPH_MAX_SHARE", 0.5),
            rrf_k=_env_int("RRF_K", 60),
            graph_min_score=_env_float("GRAPH_MIN_SCORE", 0.30),
            graph_max_score=_env_float("GRAPH_MAX_SCORE", 0.95),
            graph_keyword_min_coverage=_env_float("GRAPH_MIN_COVERAGE", 0.34),
            cache_ttl_seconds=_env_int("CACHE_TTL_SECONDS", 30 * 60),
            cache_max_entries=_env_int("CACHE_MAX_ENTRIES", 1000),
            ticket_idempotency_seconds=_env_int("TICKET_IDEMPOTENCY_SECONDS", 600),
            ticket_require_intent=_env_flag("TICKET_REQUIRE_INTENT", True),
            mysql_url=os.getenv("MYSQL_URL", "").strip(),
            qdrant_path=os.getenv("QDRANT_PATH", "").strip(),
            qdrant_url=os.getenv("QDRANT_URL", "").strip(),
            qdrant_collection=os.getenv("QDRANT_COLLECTION", "rules_chunks").strip(),
            embedding_model_path=os.getenv("EMBEDDING_MODEL_PATH", "").strip(),
            neo4j_uri=os.getenv("NEO4J_URI", "").strip(),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j").strip(),
            neo4j_password=os.getenv("NEO4J_PASSWORD", "").strip(),
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.deepseek_api_key) or self.llm_mode == "fake"

    @property
    def effective_token_secret(self) -> bytes:
        """token 签名密钥：未配置时退化为进程内随机值（重启即失效，仅限单机）。"""
        return (self.token_secret or _EPHEMERAL_SECRET).encode("utf-8")


# 未配置 AUTH_TOKEN_SECRET 时的兜底密钥：进程内随机，重启后已签发 token 全部失效
_EPHEMERAL_SECRET = secrets.token_urlsafe(32)
