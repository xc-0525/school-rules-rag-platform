"""FastAPI 应用入口（v2 正式工程）。

启动：cd backend && uvicorn app.main:app --reload --port 8000
旧极简版仍可用：cd backend && uvicorn main:app --reload --port 8001（现场回退）
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agents.graph import build_graph
from app.api.v1.auth import build_router as build_auth_router
from app.api.v1.chat import build_router
from app.api.v1.conversations import build_router as build_conversations_router
from app.api.v1.documents import build_router as build_documents_router
from app.core.config import BASE_DIR, Settings
from app.services.container import Services, build_services


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """应用工厂：测试时可注入自定义 Settings / Services（例如 FakeLLM）。"""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    logger = logging.getLogger("app.main")
    settings = settings or Settings.from_env()
    services = services or build_services(settings)
    nodes, entry = build_graph(settings)
    logger.info(
        "create_app modes vector=%s graph=%s llm=%s auth=%s tools=%s",
        settings.vector_mode,
        settings.graph_mode,
        settings.llm_mode,
        settings.auth_required,
        settings.max_tool_rounds,
    )

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

        # 应用退出：关闭复用的 Neo4j driver / Qdrant 本地客户端
        for service in (services.graph, services.vector):
            close = getattr(service, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    app = FastAPI(
        title=f"{settings.app_name} · v2",
        version=settings.version,
        lifespan=lifespan,
        description=(
            "**自研 Agent 图引擎**：10+ 节点，支持并行 / 选择 / 循环三种控制结构，"
            "每个节点执行通过 SSE `trace` 事件实时输出。\n\n"
            "接口：`POST /api/v1/chat`（流式问答）、`GET /api/v1/graph`（图定义）、`GET /health`。"
        ),
    )
    app.state.settings = settings
    app.state.services = services
    app.state.nodes = nodes
    app.state.entry = entry

    # 前端（Vite dev server）联调用
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(build_auth_router(settings))
    app.include_router(build_router(services, settings, nodes, entry))
    app.include_router(build_conversations_router(services, settings))
    app.include_router(build_documents_router(services, settings))

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
        return {
            "status": "ok",
            "version": settings.version,
            "modes": {
                "vector": settings.vector_mode,
                "graph": settings.graph_mode,
                "llm": settings.llm_mode,
                "persistence": "mysql" if settings.mysql_url else "mock",
                "auth": "required" if settings.auth_required else "optional",
                "registration": "open" if settings.allow_registration else "closed",
            },
            "llm_configured": settings.llm_configured,
            "vector_ready": getattr(app.state, "vector_ready", None),
            "graph": {
                "node_count": len(nodes),
                "agent_node_count": sum(1 for spec in nodes.values() if spec.kind == "agent"),
                "control_structures": sorted(
                    {spec.control for spec in nodes.values() if spec.control}
                ),
            },
        }

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
