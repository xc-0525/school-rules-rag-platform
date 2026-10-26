"""文档管理接口：上传入库 / 列表 / 详情 / 删除。

上传接口 POST /api/v1/documents/upload：
- 表单字段：file（必填，.md/.txt）、title（可选）、replace（可选，默认 true=同名替换）
- 处理流程：保存文件 → 解析「第X章/第N条」→ 写入 MySQL + Qdrant + Neo4j
- 复用后端已加载的 BGE 模型与 Qdrant 客户端（本地模式同目录只能一个进程打开）
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.config import BASE_DIR, Settings
from app.db.session import new_session
from app.schemas.document import (
    ChunkPreview,
    DeleteResponse,
    DocumentDetail,
    DocumentItem,
    DocumentUploadResponse,
    UploadTextRequest,
)
from app.services.ingestion import RulesIngestor
from app.services.rules_parser import parse_file

ALLOWED_SUFFIXES = {".md", ".txt"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB
UPLOAD_DIR = BASE_DIR / "data" / "uploads"


def _iso(value: Any) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _list_documents_sync(mysql_url: str) -> list[dict[str, Any]]:
    from app.db.models import Document

    with new_session(mysql_url) as session:
        rows = session.execute(select(Document).order_by(Document.id.desc())).scalars().all()
        return [
            {
                "id": row.id,
                "title": row.title,
                "file_name": row.file_name,
                "file_type": row.file_type,
                "file_size": row.file_size,
                "chunk_count": row.chunk_count,
                "status": row.status,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            }
            for row in rows
        ]


def _get_document_sync(
    mysql_url: str, document_id: int, preview: int = 20
) -> dict[str, Any] | None:
    from app.db.models import Chunk, Document

    with new_session(mysql_url) as session:
        document = session.get(Document, document_id)
        if document is None:
            return None
        chunks = (
            session.execute(
                select(Chunk)
                .where(Chunk.document_id == document_id)
                .order_by(Chunk.chunk_index)
                .limit(preview)
            )
            .scalars()
            .all()
        )
        return {
            "id": document.id,
            "title": document.title,
            "file_name": document.file_name,
            "file_type": document.file_type,
            "file_size": document.file_size,
            "chunk_count": document.chunk_count,
            "status": document.status,
            "created_at": _iso(document.created_at),
            "updated_at": _iso(document.updated_at),
            "file_path": document.file_path,
            "chunks": [
                ChunkPreview(
                    id=chunk.id,
                    chapter=chunk.chapter,
                    chunk_index=chunk.chunk_index,
                    qdrant_point_id=chunk.qdrant_point_id,
                    content=chunk.content,
                )
                for chunk in chunks
            ],
        }


def _unlink_upload(file_path: str) -> None:
    try:
        path = Path(file_path)
        if path.is_file() and UPLOAD_DIR in path.parents:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def build_router(services: Any, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["文档"])

    def require_mysql() -> None:
        if not settings.mysql_url:
            raise HTTPException(status_code=400, detail="未配置 MYSQL_URL，文档管理不可用")

    def require_real_ingestion() -> None:
        if settings.vector_mode != "qdrant":
            raise HTTPException(
                status_code=400,
                detail="当前为 mock 向量模式，无法入库；请设置 VECTOR_MODE=qdrant 并重启后端",
            )
        require_mysql()

    @router.post(
        "/documents/upload",
        response_model=DocumentUploadResponse,
        summary="上传校规文档并入库（MySQL + Qdrant + Neo4j）",
    )
    async def upload_document(payload: UploadTextRequest):
        safe_name = Path(payload.filename or "").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"暂只支持 {sorted(ALLOWED_SUFFIXES)}，当前文件：{safe_name or '未知'}",
            )

        data = payload.content.encode("utf-8")
        if not data:
            raise HTTPException(status_code=400, detail="文件内容为空")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件超过 5MB 限制")

        require_real_ingestion()

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)

        document = parse_file(target)
        document.file_name = safe_name
        document.file_path = str(target)
        if payload.title and payload.title.strip():
            document.title = payload.title.strip()
        if not document.chunks:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="未解析到「第N条」条款，请检查文档格式")

        ingestor = RulesIngestor(settings, vector_store=services.vector)
        try:
            result = await asyncio.to_thread(ingestor.ingest_parsed, document, payload.replace)
        except Exception as exc:  # noqa: BLE001
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500, detail=f"入库失败：{type(exc).__name__}: {exc}"
            ) from exc

        return DocumentUploadResponse(
            document_id=result["document_id"],
            title=result["title"],
            file_name=result["file_name"],
            chunk_count=result["chunk_count"],
            chapters=result["chapters"],
            status="done",
            message="入库成功（同名文件已替换）" if payload.replace else "入库成功",
        )

    @router.get("/documents", response_model=list[DocumentItem], summary="文档列表")
    async def list_documents():
        require_mysql()
        return await asyncio.to_thread(_list_documents_sync, settings.mysql_url)

    @router.get(
        "/documents/{document_id}",
        response_model=DocumentDetail,
        summary="文档详情（含分块预览）",
    )
    async def get_document(document_id: int):
        require_mysql()
        data = await asyncio.to_thread(_get_document_sync, settings.mysql_url, document_id)
        if data is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        return data

    @router.delete(
        "/documents/{document_id}",
        response_model=DeleteResponse,
        summary="删除文档（同时清理 Qdrant 向量与 Neo4j 图谱）",
    )
    async def delete_document(document_id: int):
        require_real_ingestion()
        data = await asyncio.to_thread(_get_document_sync, settings.mysql_url, document_id)
        if data is None:
            raise HTTPException(status_code=404, detail="文档不存在")

        ingestor = RulesIngestor(settings, vector_store=services.vector)
        deleted = await asyncio.to_thread(ingestor.delete_document, document_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="文档不存在")
        _unlink_upload(data["file_path"])
        return DeleteResponse(deleted=True, document_id=document_id)

    return router
