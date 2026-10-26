"""文档管理 API 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class UploadTextRequest(BaseModel):
    """文档上传请求。

    两种传法（二选一）：
    - .md / .txt：用 content 传 UTF-8 文本；
    - .pdf：用 content_base64 传文件内容的 base64（文字版 PDF）。

    之所以用 JSON 而不是 multipart：本机 Python 没有 python-multipart，
    且当前沙箱内无法安装；如需标准文件上传，可另装 python-multipart
    并使用 /api/v1/documents/upload-file（安装后自动出现）。
    """

    filename: str = Field(..., description="文件名，后缀 .md / .txt / .pdf", examples=["校园服务指南.md"])
    content: str | None = Field(default=None, description=".md/.txt 的 UTF-8 文本内容")
    content_base64: str | None = Field(default=None, description=".pdf 的 base64 文件内容")
    title: str | None = Field(default=None, description="自定义标题，留空取文档标题/文件名")
    replace: bool = Field(default=True, description="同名文件是否覆盖替换")

    @model_validator(mode="after")
    def _check_payload(self) -> "UploadTextRequest":
        has_text = bool(self.content and self.content.strip())
        has_base64 = bool(self.content_base64 and self.content_base64.strip())
        if has_text and has_base64:
            raise ValueError("content 与 content_base64 只能提供一个")
        if not has_text and not has_base64:
            raise ValueError("content 与 content_base64 必须提供一个")
        return self



class DocumentItem(BaseModel):
    """文档列表项。"""

    id: int
    title: str
    file_name: str
    file_type: str
    file_size: int = 0
    chunk_count: int = 0
    status: str = "pending"
    created_at: str | None = None
    updated_at: str | None = None


class ChunkPreview(BaseModel):
    """分块预览。"""

    id: int
    chapter: str | None = None
    chunk_index: int = 0
    qdrant_point_id: str | None = None
    content: str


class DocumentDetail(DocumentItem):
    """文档详情（含分块预览）。"""

    chunks: list[ChunkPreview] = Field(default_factory=list)


class DocumentUploadResponse(BaseModel):
    """上传入库结果。"""

    document_id: int
    title: str
    file_name: str
    chunk_count: int
    chapters: int
    status: str = "done"
    message: str = "入库成功"


class DeleteResponse(BaseModel):
    """删除结果。"""

    deleted: bool
    document_id: int
