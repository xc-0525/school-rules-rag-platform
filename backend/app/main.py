"""FastAPI 应用入口（v2 正式工程）。

启动：cd backend && uvicorn app.main:app --reload --port 8000
旧极简版仍可用：cd backend && uvicorn main:app --reload --port 8001（现场回退）
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents.graph import build_graph
from app.api.v1.auth import build_router as build_auth_router
from app.api.v1.chat import build_router
from app.api.v1.conversations import build_router as build_conversations_router
from app.api.v1.documents import build_router as build_documents_router
from app.api.v1.lostfound import build_router as build_lostfound_router
from app.api.v1.admin_users import build_router as build_admin_users_router
from app.api.v1.admin_feedback import build_router as build_admin_feedback_router
from app.api.v1.admin_analytics import build_router as build_admin_analytics_router
from app.api.v1.notices import build_router as build_notices_router
from app.api.v1.repairs import build_router as build_repairs_router
from app.api.v1.reservations import build_router as build_reservations_router
from app.api.v1.tickets import build_router as build_tickets_router
from app.core.config import BASE_DIR, Settings
from app.core.executors import shutdown_ingest_pool
from app.core.ratelimit import RateLimiter
from app.core.request_context import RequestIdFilter, install_request_id
from app.services.auth import resolve_token
from app.services.container import Services, build_services


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """应用工厂：测试时可注入自定义 Settings / Services（例如 FakeLLM）。"""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
        )
    # 已有 handler（uvicorn / 测试框架）也要能渲染 request_id 字段
    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, RequestIdFilter) for item in handler.filters):
            handler.addFilter(RequestIdFilter())
    logger = logging.getLogger("app.main")
    settings = settings or Settings.from_env()
    services = services or build_services(settings)
    nodes, entry = build_graph(settings)
    logger.info(
        "create_app modes vector=%s graph=%s llm=%s auth=%s dev_mode=%s tools=%s",
        settings.vector_mode,
        settings.graph_mode,
        settings.llm_mode,
        settings.auth_required,
        settings.dev_mode,
        settings.max_tool_rounds,
    )
    # 多 worker 部署前必须配置 AUTH_TOKEN_SECRET，否则各 worker 签名密钥不同，
    # 会出现"登录成功但下一个请求 401"的随机故障。
    if not settings.token_secret:
        logger.warning(
            "未配置 AUTH_TOKEN_SECRET：token 签名密钥为进程内随机值，"
            "重启后全部失效，且无法多 worker 部署。"
        )
    if settings.dev_mode:
        logger.warning("DEV_MODE=1：已放开匿名访问（匿名身份为独立的 anonymous 账号）")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """启动预热：真实 Qdrant 模式下提前加载 BGE 模型 + 建好种子集合。

        BGE 首次加载约 1~2 分钟，预热后问答才不会被首次加载拖慢。
        """
        if settings.vector_mode == "qdrant":
            try:
                await asyncio.to_thread(services.vector.ensure_ready)
                app.state.vector_ready = True
                print("[startup] 向量服务就绪：Qdrant 本地嵌入式 + 本地 BGE")
            except Exception as exc:  # noqa: BLE001
                app.state.vector_ready = False
                print(f"[startup] 向量服务初始化失败：{exc}")
        else:
            app.state.vector_ready = True
        yield

        # 应用退出：关闭复用的 Neo4j driver / Qdrant 本地客户端 / 入库线程池
        for service in (services.graph, services.vector):
            close = getattr(service, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        shutdown_ingest_pool()

    app = FastAPI(
        title=f"{settings.app_name} · v2",
        version=settings.version,
        lifespan=lifespan,
        # P1-3：生产档位（AUTH_REQUIRED=1）关闭交互式文档与 API 清单，
        # 防止公网经 nginx（此前显式代理 /docs）枚举全部接口。本地 dev 保留。
        docs_url=None if settings.auth_required else "/docs",
        redoc_url=None if settings.auth_required else "/redoc",
        openapi_url=None if settings.auth_required else "/openapi.json",
        description=(
            "**自研 Agent 图引擎**：10+ 节点，支持并行 / 选择 / 循环三种控制结构，"
            "每个节点执行通过 SSE `trace` 事件实时输出。\n\n"
            "接口：`POST /api/v1/chat`（流式问答）、`GET /api/v1/graph`（图定义）、`GET /health`。"
        ),
    )
    install_request_id(app)
    app.state.settings = settings
    app.state.services = services
    app.state.nodes = nodes
    app.state.entry = entry
    limiter = RateLimiter(
        per_minute=settings.rate_limit_per_minute,
        per_day=settings.rate_limit_per_day,
        concurrent=settings.rate_limit_concurrent,
    )
    app.state.limiter = limiter

    # 前端（Vite dev server）联调用
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """P0-3：按用户（有 token）或 IP 做每分钟限流。

        只拦 /api/ 前缀：/health、/ready、静态资源与前端页面不受影响，
        否则探针被限流会误判服务不可用。
        """
        if not settings.rate_limit_enabled or not request.url.path.startswith("/api/"):
            return await call_next(request)
        # 只读请求不计数：一页要拉报修/预约/失物/位置/余量 5 个接口，
        # 翻两次页签就打满分钟配额，页面会整块空白（实测踩到）。
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        user = resolve_token(request.headers.get("authorization"))
        client_host = request.client.host if request.client else "unknown"
        key = f"u:{user.id}" if user else f"ip:{client_host}"
        allowed, retry_after = limiter.hit(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求过于频繁，请 {retry_after} 秒后重试"},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    app.include_router(build_auth_router(settings, limiter=limiter))
    app.include_router(build_router(services, settings, nodes, entry, limiter=limiter))
    app.include_router(build_conversations_router(services, settings))
    app.include_router(build_documents_router(services, settings))
    app.include_router(build_tickets_router(settings))
    app.include_router(build_notices_router(settings))
    app.include_router(build_repairs_router(settings))
    app.include_router(build_reservations_router(settings))
    app.include_router(build_lostfound_router(settings))
    app.include_router(build_admin_users_router(settings))
    app.include_router(build_admin_feedback_router(settings))
    app.include_router(build_admin_analytics_router(settings))

    # 单端口演示：SERVE_FRONTEND=1 且 frontend/dist 存在时，后端直接托管前端
    frontend_dist = BASE_DIR.parent / "frontend" / "dist"
    frontend_index = frontend_dist / "index.html"
    serve_frontend = settings.serve_frontend and frontend_index.is_file()
    if serve_frontend:
        assets_dir = frontend_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/health", tags=["运维"], summary="健康检查")
    async def health() -> dict[str, Any]:
        # P1-3：公开面只保留探活必需与前端依赖字段（App.vue 用 modes.vector/graph、
        # vector_ready）；auth/dev_mode/token/rate_limit/registration/llm_configured
        # 属部署敏感档位，仅 DEV_MODE 下返回。
        payload: dict[str, Any] = {
            "status": "ok",
            "version": settings.version,
            "modes": {
                "vector": settings.vector_mode,
                "graph": settings.graph_mode,
                "llm": settings.llm_mode,
                "persistence": "mysql" if settings.mysql_url else "mock",
            },
            "vector_ready": getattr(app.state, "vector_ready", None),
            "graph": {
                "node_count": len(nodes),
                "agent_node_count": sum(1 for spec in nodes.values() if spec.kind == "agent"),
                "control_structures": sorted(
                    {spec.control for spec in nodes.values() if spec.control}
                ),
            },
        }
        if settings.dev_mode:
            payload["modes"].update(
                {
                    "auth": "required" if settings.auth_required else "dev-anonymous",
                    "dev_mode": settings.dev_mode,
                    "token": "signed" if settings.token_secret else "ephemeral-secret",
                    "rate_limit": "on" if settings.rate_limit_enabled else "off",
                    "registration": "open" if settings.allow_registration else "closed",
                }
            )
            payload["llm_configured"] = settings.llm_configured
        return payload

    @app.get("/ready", include_in_schema=False)
    async def ready() -> dict[str, Any]:
        """容器编排用就绪探针：向量依赖未就绪时返回 503。"""
        issues: list[str] = []
        if settings.vector_mode == "qdrant" and not getattr(app.state, "vector_ready", False):
            issues.append("vector_not_ready")
        if issues:
            raise HTTPException(status_code=503, detail={"ready": False, "issues": issues})
        return {"ready": True}

    @app.get("/", include_in_schema=False)
    async def root():
        if serve_frontend:
            return FileResponse(frontend_index)
        return {
            "name": f"{settings.app_name} · v2",
            "docs": "/docs",
            "chat": "POST /api/v1/chat",
            "graph": "GET /api/v1/graph",
            "legacy_demo": "极简版入口：uvicorn main:app --port 8001",
            "hint": "设置 SERVE_FRONTEND=1 可直接在 8000 端口打开前端",
        }

    if serve_frontend:

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            """SPA 路由回退：/chat、/history 等交给前端 index.html。"""
            if full_path.startswith(
                ("api/", "docs", "openapi.json", "redoc", "health", "assets")
            ):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (frontend_dist / full_path).resolve()
            if full_path and frontend_dist.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_index)

    return app


app = create_app()
