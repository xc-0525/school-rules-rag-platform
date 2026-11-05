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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.config import BASE_DIR, Settings
from app.core.executors import run_in_ingest_pool
from app.db.session import new_session
from app.schemas.document import (
    ChunkPreview,
    DeleteResponse,
    DocumentDetail,
    DocumentItem,
    DocumentUploadResponse,
    UploadTextRequest,
)
from app.services.auth import AuthUser, build_current_user_dependency
from app.services.ingestion import GraphUnavailableError, RulesIngestor
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
    current_user = build_current_user_dependency(settings)

    def ensure_admin(user: AuthUser) -> None:
        """上传/删除文档仅管理员可操作。

        P0-1 修复：旧实现写作 `if settings.auth_required and (...)`，
        在 AUTH_REQUIRED=0（手动 uvicorn 启动）时会直接放行，等于把知识库
        的写权限开放给任何人。现在与鉴权开关解耦：只要不是显式的 DEV_MODE，
        就必须是 admin。
        """
        if settings.dev_mode:
            # 本地/测试模式：允许匿名联调，但身份记为 anonymous（不再写 user_id=1）
            return
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可管理校规文档")

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
        raw: bytes | None,
        filename: str,
        title: str | None,
        replace: bool,
        uploader: AuthUser,
        target_path: Path | None = None,
    ) -> DocumentUploadResponse:
        """上传公共逻辑：保存 → 解析 → 三库入库。

        两种来源：
        - `raw`：旧 JSON 通道（base64 解码后的 bytes）；
        - `target_path`：新 raw 通道已经边收边落盘的文件（避免整文件驻留内存）。
        """
        safe_name = Path(filename or "upload").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"暂只支持 {sorted(ALLOWED_SUFFIXES)}，当前文件：{safe_name or '未知'}",
            )
        limit = MAX_PDF_BYTES if suffix == ".pdf" else MAX_TEXT_BYTES
        if target_path is None:
            if not raw:
                raise HTTPException(status_code=400, detail="文件内容为空")
            if len(raw) > limit:
                raise HTTPException(
                    status_code=413, detail=f"文件超过 {limit // (1024 * 1024)}MB 限制"
                )
        else:
            if not target_path.is_file() or target_path.stat().st_size == 0:
                target_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="文件内容为空")
            if target_path.stat().st_size > limit:
                target_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413, detail=f"文件超过 {limit // (1024 * 1024)}MB 限制"
                )

        require_real_ingestion()

        # 先做依赖预检（Qdrant / BGE / Neo4j），避免写了一半才发现依赖不可用
        ingestor = RulesIngestor(settings, vector_store=services.vector)
        try:
            await run_in_ingest_pool(ingestor.preflight)
        except GraphUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail=f"入库依赖预检失败：{type(exc).__name__}: {exc}"
            ) from exc

        if target_path is not None:
            target = target_path
        else:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
            target.write_bytes(raw)

        try:
            if suffix == ".pdf":
                # 只读一次：raw 通道下这里才会把文件读进内存（约 1 倍文件大小）
                data = raw if raw is not None else target.read_bytes()
                try:
                    document = parse_pdf_bytes(data, filename=safe_name, title=title)
                except PDFTextError:
                    # 文字层为空 → 自动尝试 OCR（装了引擎才会成功）
                    document = await run_in_ingest_pool(
                        ocr_pdf_bytes, data, safe_name, title
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

        try:
            result = await run_in_ingest_pool(
                ingestor.ingest_parsed, document, replace, uploader.id
            )
        except GraphUnavailableError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500, detail=f"入库失败：{type(exc).__name__}: {exc}"
            ) from exc

        # 新文档入库后清空问题缓存，避免旧问题继续命中旧答案/旧来源
        if hasattr(services.cache, "clear"):
            services.cache.clear()

        dedupe = result.get("dedupe")
        if result.get("skipped_all"):
            message = (
                f"全部 {dedupe['total']} 条条款均已在库中，未重复入库"
                if dedupe
                else "全部条款均已存在，未重复入库"
            )
        elif replace:
            message = "入库成功（同名文件已替换）"
        else:
            message = "入库成功"
        if dedupe and not result.get("skipped_all"):
            message = f"新增 {dedupe['added']} 条，跳过重复 {dedupe['duplicates']} 条"

        return DocumentUploadResponse(
            document_id=result["document_id"],
            title=result["title"],
            file_name=result["file_name"],
            chunk_count=result["chunk_count"],
            chapters=result["chapters"],
            status="done",
            message=message,
            dedupe=dedupe,
        )

    # ---------------- JSON 上传（默认可用，无需任何新依赖） ----------------
    @router.post(
        "/documents/upload",
        response_model=DocumentUploadResponse,
        summary="上传文档并入库（JSON：文本或 PDF base64）",
    )
    async def upload_document(
        payload: UploadTextRequest, user: AuthUser = Depends(current_user)
    ):
        ensure_admin(user)
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
            return await _ingest_upload(raw, safe_name, payload.title, payload.replace, user)

        if not payload.content:
            raise HTTPException(status_code=400, detail=".md/.txt 需通过 content 传文本内容")
        return await _ingest_upload(
            payload.content.encode("utf-8"), safe_name, payload.title, payload.replace, user
        )

    # ---------------- multipart 上传（装了 python-multipart 后才出现） ----------------
    if HAS_MULTIPART:

        @router.post(
            "/documents/upload-file",
            response_model=DocumentUploadResponse,
            summary="上传文档文件（multipart，安装 python-multipart 后可用）",
        )
        async def upload_document_file(
            file: UploadFile = File(..., description=".md / .txt / 文字版 .pdf"),
            title: str | None = Form(default=None),
            replace: bool = Form(default=True),
            user: AuthUser = Depends(current_user),
        ):
            ensure_admin(user)
            raw = await file.read()
            return await _ingest_upload(
                raw, Path(file.filename or "upload").name, title, replace, user
            )

    # ---------------- 原始字节流上传（推荐：不做 base64，边收边落盘） ----------------
    @router.post(
        "/documents/upload-raw",
        response_model=DocumentUploadResponse,
        summary="上传文档（application/octet-stream，边收边落盘）",
    )
    async def upload_document_raw(
        request: Request,
        filename: str = Query(..., description="原始文件名（含扩展名）"),
        title: str | None = Query(default=None),
        replace: bool = Query(default=True),
        user: AuthUser = Depends(current_user),
    ):
        """P2-11：不经 base64，直接接收文件字节流。

        旧 JSON 通道要把文件编码成 base64（体积 +33%），服务端再解码一份，
        20MB 的 PDF 峰值能到 100MB 以上。这里改成边收边写：内存占用与文件大小
        无关，且累计超限立刻中止并删除半成品。
        """
        ensure_admin(user)  # 先鉴权再读 body，未授权请求不会写磁盘

        safe_name = Path(filename or "").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"暂只支持 {sorted(ALLOWED_SUFFIXES)}，当前文件：{safe_name or '未知'}",
            )
        limit = MAX_PDF_BYTES if suffix == ".pdf" else MAX_TEXT_BYTES

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        total = 0
        try:
            with target.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"文件超过 {limit // (1024 * 1024)}MB 限制",
                        )
                    handle.write(chunk)
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 —— 网络中断等
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400, detail=f"上传中断：{type(exc).__name__}"
            ) from exc

        if total == 0:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="文件内容为空")

        return await _ingest_upload(
            None, safe_name, title, replace, user, target_path=target
        )

    # ---------------- 列表 / 详情 / 删除 ----------------
    @router.get(
        "/documents",
        response_model=list[DocumentItem],
        summary="文档列表",
    )
    async def list_documents(user: AuthUser = Depends(current_user)):
        require_mysql()
        return await asyncio.to_thread(_list_documents_sync, settings.mysql_url)

    @router.get(
        "/documents/meta/ocr-status",
        summary="OCR 引擎状态（前端据此显示提示，而不是写死“需要安装”）",
    )
    async def ocr_status_api(user: AuthUser = Depends(current_user)):  # noqa: ARG001
        from app.services.ocr_parser import ocr_available

        available = ocr_available()
        return {
            "available": available,
            "engine": "RapidOCR(onnxruntime)" if available else "",
            "hint": (
                "已启用 OCR：扫描件 PDF 会自动识别并入库"
                if available
                else "未安装 OCR 引擎：扫描件 PDF 无法入库，请执行 pip install rapidocr_onnxruntime pillow"
            ),
        }

    @router.get(
        "/documents/{document_id}/preview",
        summary="在线预览原件（只读，学生与管理员均可）",
        responses={415: {"description": "该文件类型不支持浏览器内嵌预览"}},
    )
    async def preview_document(document_id: int, user: AuthUser = Depends(current_user)):  # noqa: ARG001
        """只读预览：登录用户即可访问，不做管理员限制。

        支持类型：PDF / 图片（png/jpg/webp/gif）/ 文本（txt/md）浏览器原生渲染；
        Office 文档（doc/docx/xls 等）浏览器无法原生渲染，返回 415 由前端提示下载。
        安全校验：file_path 必须仍位于上传目录内（防数据库记录被篡改后目录穿越）。
        """
        require_mysql()
        data = await asyncio.to_thread(_get_document_sync, settings.mysql_url, document_id)
        if data is None:
            raise HTTPException(status_code=404, detail="文档不存在")

        path = Path(data["file_path"])
        # 目录穿越防御：即使 DB 里的 file_path 被改过，也只允许读可信根目录内的文件
        # （data/uploads=用户上传；<项目>/rules=抓取语料入库时登记的原始路径）
        allowed_roots = [UPLOAD_DIR.resolve(), (BASE_DIR.parent / "rules").resolve()]
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            raise HTTPException(status_code=400, detail="文件路径不合法")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="原件文件已不存在（可能被清理）")

        suffix = path.suffix.lower()
        inline_types = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".txt": "text/plain",
            ".md": "text/markdown",
        }
        media_type = inline_types.get(suffix)
        if media_type is None:
            raise HTTPException(
                status_code=415,
                detail={
                    "message": f".{suffix} 类型暂不支持在线预览，请下载后查看",
                    "suffix": suffix,
                },
            )
        if media_type.endswith(("plain", "markdown")):
            media_type += "; charset=utf-8"

        from urllib.parse import quote

        utf8_name = quote(Path(data["file_name"]).name)
        ascii_fallback = "preview" + suffix
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{utf8_name}"
                ),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=3600",
            },
        )

    @router.get(
        "/documents/{document_id}",
        response_model=DocumentDetail,
        summary="文档详情（含分块预览）",
    )
    async def get_document(document_id: int, user: AuthUser = Depends(current_user)):
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
    async def delete_document(
        document_id: int, user: AuthUser = Depends(current_user)
    ):
        ensure_admin(user)
        require_real_ingestion()
        data = await asyncio.to_thread(_get_document_sync, settings.mysql_url, document_id)
        if data is None:
            raise HTTPException(status_code=404, detail="文档不存在")

        ingestor = RulesIngestor(settings, vector_store=services.vector)
        deleted = await run_in_ingest_pool(ingestor.delete_document, document_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="文档不存在")
        _unlink_upload(data["file_path"])
        # 删除文档后清空问题缓存，避免继续返回已删除文档的来源
        if hasattr(services.cache, "clear"):
            services.cache.clear()
        return DeleteResponse(deleted=True, document_id=document_id)

    return router
