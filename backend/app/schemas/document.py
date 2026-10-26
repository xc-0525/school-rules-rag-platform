"""文档管理 API 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UploadTextRequest(BaseModel):
    """文本上传请求（.md / .txt）。

    之所以用 JSON 而不是 multipart：本机 Python 没有 python-multipart，
    且当前沙箱内无法安装；对纯文本校规文件，前端读成文本后 POST JSON 即可。
    后续接 PDF/DOCX 时再安装 python-multipart 换成 multipart 上传。
    """

    filename: str = Field(..., description="文件名，后缀需为 .md 或 .txt", examples=["校园服务指南.md"])
    content: str = Field(..., description="文件文本内容（UTF-8）")
    title: str | None = Field(default=None, description="自定义标题，留空取文档一级标题")
    replace: bool = Field(default=True, description="同名文件是否覆盖替换")


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
