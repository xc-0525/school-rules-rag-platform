"""文档管理接口：上传入库 / 列表 / 详情 / 删除。

上传方式（两种，按是否安装 python-multipart 自动切换）：
1. JSON 上传：POST /api/v1/documents/upload
   - .md / .txt：content 传 UTF-8 文本；
   - .pdf（文字版）：content_base64 传 base64 文件内容；
2. multipart 文件上传：POST /api/v1/documents/upload-file
   - 仅在安装了 python-multipart 时注册（FastAPI 的 File/Form 依赖它）。

入库流程统一：保存文件 → 解析（md/txt 或 PDF 文字层）→ MySQL + Qdrant + Neo4j。
"""

from __future__ import annotations

import asyncio
import base64
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
from app.services.ocr_parser import OCRUnavailableError, ocr_pdf_bytes
from app.services.pdf_parser import PDFTextError, parse_pdf_bytes
from app.services.rules_parser import parse_file

ALLOWED_SUFFIXES = {".md", ".txt", ".pdf"}
MAX_TEXT_BYTES = 5 * 1024 * 1024      # .md/.txt
MAX_PDF_BYTES = 20 * 1024 * 1024      # .pdf
UPLOAD_DIR = BASE_DIR / "data" / "uploads"

try:  # 可选依赖：装了 python-multipart 才注册 multipart 上传接口
    import multipart as _multipart  # noqa: F401

    HAS_MULTIPART = True
except ImportError:
    HAS_MULTIPART = False


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

    async def _ingest_upload(
        raw: bytes,
        filename: str,
        title: str | None,
        replace: bool,
    ) -> DocumentUploadResponse:
        """上传公共逻辑：保存 → 解析 → 三库入库。"""
        safe_name = Path(filename or "upload").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"暂只支持 {sorted(ALLOWED_SUFFIXES)}，当前文件：{safe_name or '未知'}",
            )
        if not raw:
            raise HTTPException(status_code=400, detail="文件内容为空")
        limit = MAX_PDF_BYTES if suffix == ".pdf" else MAX_TEXT_BYTES
        if len(raw) > limit:
            raise HTTPException(status_code=413, detail=f"文件超过 {limit // (1024 * 1024)}MB 限制")

        require_real_ingestion()

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(raw)

        try:
            if suffix == ".pdf":
                try:
                    document = parse_pdf_bytes(raw, filename=safe_name, title=title)
                except PDFTextError:
                    # 文字层为空 → 自动尝试 OCR（装了引擎才会成功）
                    document = await asyncio.to_thread(
                        ocr_pdf_bytes, raw, safe_name, title
                    )
            else:
                document = parse_file(target)
                document.file_name = safe_name
                if title and title.strip():
                    document.title = title.strip()
        except OCRUnavailableError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PDFTextError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400, detail=f"解析失败：{type(exc).__name__}: {exc}"
            ) from exc

        if not document.chunks:
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail="未解析到「第N条」条款，请检查文档格式（PDF 可能是扫描件，需要 OCR）",
            )
        document.file_path = str(target)

        ingestor = RulesIngestor(settings, vector_store=services.vector)
        try:
            result = await asyncio.to_thread(ingestor.ingest_parsed, document, replace)
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
            message="入库成功（同名文件已替换）" if replace else "入库成功",
        )

    # ---------------- JSON 上传（默认可用，无需任何新依赖） ----------------
    @router.post(
        "/documents/upload",
        response_model=DocumentUploadResponse,
        summary="上传文档并入库（JSON：文本或 PDF base64）",
    )
    async def upload_document(payload: UploadTextRequest):
        safe_name = Path(payload.filename or "").name
        suffix = Path(safe_name).suffix.lower()

        if suffix == ".pdf":
            if not payload.content_base64:
                raise HTTPException(
                    status_code=400, detail="PDF 需通过 content_base64 传文件内容（base64）"
                )
            try:
                raw = base64.b64decode("".join(payload.content_base64.split()), validate=True)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail="content_base64 不是合法的 base64") from exc
            return await _ingest_upload(raw, safe_name, payload.title, payload.replace)

        if not payload.content:
            raise HTTPException(status_code=400, detail=".md/.txt 需通过 content 传文本内容")
        return await _ingest_upload(
            payload.content.encode("utf-8"), safe_name, payload.title, payload.replace
        )

    # ---------------- multipart 上传（装了 python-multipart 后才出现） ----------------
    if HAS_MULTIPART:
        from fastapi import File, Form, UploadFile

        @router.post(
            "/documents/upload-file",
            response_model=DocumentUploadResponse,
            summary="上传文档文件（multipart，安装 python-multipart 后可用）",
        )
        async def upload_document_file(
            file: UploadFile = File(..., description=".md / .txt / 文字版 .pdf"),
            title: str | None = Form(default=None),
            replace: bool = Form(default=True),
        ):
            raw = await file.read()
            return await _ingest_upload(
                raw, Path(file.filename or "upload").name, title, replace
            )

    # ---------------- 列表 / 详情 / 删除 ----------------
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
