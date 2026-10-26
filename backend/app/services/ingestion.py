"""校规文档入库：一次写入 MySQL（元数据）+ Qdrant（向量）+ Neo4j（图谱）。

两种用法：
1. 命令行（scripts/ingest_rules.py）：独立进程，自己打开 Qdrant 本地库；
2. 上传接口（POST /api/v1/documents/upload）：复用后端已加载的 BGE 模型和
   Qdrant 客户端（Qdrant 本地模式同一目录只能被一个进程打开，所以必须复用），
   由 RulesIngestor(settings, vector_store=services.vector) 注入。

流程：
rules_parser 解析 → MySQL documents/chunks → BGE 向量化 → Qdrant upsert
→ Neo4j：文档 → 章节 → 条款 → 主题。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR, Settings
from app.db.session import new_session
from app.services.embedding import BGEEmbedder
from app.services.rules_parser import ParsedDocument, load_documents

DEMO_USER_ID = 1

# 关键词 → 主题（写入图谱 Topic 节点，供多跳检索与展示）
TOPIC_RULES: dict[str, list[str]] = {
    "宿舍管理": ["宿舍", "熄灯", "晚归", "夜不归宿", "电器"],
    "考试纪律": ["考试", "作弊", "学术", "论文", "抄袭"],
    "奖助学金": ["奖学金", "助学金", "励志", "资助"],
    "考勤请假": ["请假", "考勤", "旷课", "缺课"],
    "违纪处分": ["处分", "打架", "斗殴", "偷窃", "开除", "记过", "留校察看"],
    "学籍管理": ["学籍", "注册", "转专业", "休学", "退学", "补考", "重修"],
    "校园服务": ["图书馆", "食堂", "校园卡", "医疗", "心理", "就业", "网络", "场馆"],
    "总则": ["总则", "附则", "解释", "施行"],
}


def classify_topics(chapter: str, content: str) -> list[str]:
    """根据章节名和条款内容归类主题。"""
    text = f"{chapter}{content}"
    topics = [
        topic for topic, keywords in TOPIC_RULES.items() if any(keyword in text for keyword in keywords)
    ]
    return topics or ["其他"]


def _resolve_path(path: str, default: str) -> str:
    value = path or default
    candidate = Path(value)
    return str(candidate if candidate.is_absolute() else (BASE_DIR / candidate).resolve())


@dataclass
class IngestResult:
    """入库结果统计。"""

    documents: int = 0
    chunks: int = 0
    qdrant_points: int = 0
    neo4j_rules: int = 0
    titles: list[str] | None = None

    def __str__(self) -> str:
        return (
            f"入库完成：文档 {self.documents} 份，条款 {self.chunks} 条，"
            f"Qdrant 向量 {self.qdrant_points} 个，Neo4j 条款节点 {self.neo4j_rules} 个"
        )


class RulesIngestor:
    """把校规文档同时写入三库。"""

    def __init__(self, settings: Settings, vector_store: Any = None) -> None:
        if not settings.embedding_model_path:
            raise RuntimeError("未配置 EMBEDDING_MODEL_PATH（指向本机 bge-small-zh-v1.5 目录）")
        self.settings = settings
        self.vector_store = vector_store
        self.embedder = getattr(vector_store, "embedder", None) or BGEEmbedder(
            settings.embedding_model_path
        )
        self.qdrant_path = _resolve_path(settings.qdrant_path, "data/qdrant")
        self.collection = settings.qdrant_collection
        self.mysql_url = settings.mysql_url
        self.neo4j_uri = settings.neo4j_uri or "bolt://127.0.0.1:7687"
        self.neo4j_auth = (
            (settings.neo4j_user, settings.neo4j_password)
            if (settings.neo4j_user or settings.neo4j_password)
            else None
        )
        self._client: Any = None

    # ------------------------------------------------------------------ 入口
    def run(self, directory: str | Path, reset: bool = False) -> IngestResult:
        """命令行入口：整目录入库。"""
        documents = load_documents(directory)
        if not documents:
            raise RuntimeError(f"目录中没有可解析的 .md/.txt 文档：{directory}")

        self.ensure_ready(reset=reset)
        if reset:
            self._reset_mysql()
            self._reset_neo4j()

        result = IngestResult(titles=[])
        for document in documents:
            outcome = self.ingest_parsed(document, replace=False)
            result.documents += 1
            result.chunks += outcome["chunk_count"]
            result.qdrant_points += outcome["chunk_count"]
            result.neo4j_rules += outcome["chunk_count"]
            assert result.titles is not None
            result.titles.append(document.title)
        return result

    def ingest_parsed(self, document: ParsedDocument, replace: bool = True) -> dict[str, Any]:
        """上传接口入口：入库单个已解析文档，返回 document_id 等信息。"""
        self.ensure_ready()
        if replace:
            self._delete_by_file_name(document.file_name)
        return self._ingest_document(document)

    # ------------------------------------------------------------ 初始化
    def ensure_ready(self, reset: bool = False) -> None:
        """加载模型 / 连接 Qdrant / 确保集合存在。"""
        from qdrant_client.models import Distance, VectorParams

        self.embedder.ensure_loaded()

        if self.vector_store is not None:
            # 复用后端已加载的 Qdrant 客户端（本地模式同目录只能一个进程打开）
            self.vector_store.ensure_ready()
            self._client = self.vector_store._client
            self.collection = self.vector_store.collection
        elif self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(path=self.qdrant_path)

        existing = {item.name for item in self._client.get_collections().collections}
        if reset and self.collection in existing:
            self._client.delete_collection(self.collection)
            existing.discard(self.collection)
        if self.collection not in existing:
            dimension = len(self.embedder.encode_one("测试"))
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )

    # ------------------------------------------------------------ 删除（用于替换/删除文档）
    def delete_document(self, document_id: int) -> bool:
        """删除一份文档在三个库里的全部数据。"""
        from sqlalchemy import select

        from app.db.models import Chunk, Document

        self.ensure_ready()
        with new_session(self.mysql_url) as session:
            document = session.get(Document, document_id)
            if document is None:
                return False
            point_ids = [
                row[0]
                for row in session.execute(
                    select(Chunk.qdrant_point_id).where(Chunk.document_id == document_id)
                ).all()
                if row[0]
            ]
            title = document.title
            session.delete(document)
            session.commit()

        if point_ids:
            self._client.delete(collection_name=self.collection, points_selector=point_ids)
        self._delete_neo4j_document(document_id)
        return True

    def _delete_by_file_name(self, file_name: str) -> int | None:
        """按文件名删除旧文档（上传同名文件时先替换）。"""
        from sqlalchemy import select

        from app.db.models import Document

        with new_session(self.mysql_url) as session:
            document_id = session.execute(
                select(Document.id).where(Document.file_name == file_name)
            ).scalar_one_or_none()
        if document_id is not None:
            self.delete_document(int(document_id))
        return document_id

    def _delete_neo4j_document(self, document_id: int) -> None:
        from neo4j import GraphDatabase

        cypher = """
        MATCH (d:RuleDocument {document_id: $document_id})
        OPTIONAL MATCH (d)-[:HAS_CHAPTER]->(c:Chapter)-[:HAS_RULE]->(r:Rule)
        DETACH DELETE r, c, d
        """
        driver = GraphDatabase.driver(self.neo4j_uri, auth=self.neo4j_auth)
        try:
            with driver.session(database="neo4j") as session:
                session.run(cypher, document_id=document_id)
        finally:
            driver.close()

    # ------------------------------------------------------------ 清空（命令行 --reset）
    def _reset_mysql(self) -> None:
        from sqlalchemy import text

        with new_session(self.mysql_url) as session:
            session.execute(text("DELETE FROM documents"))
            session.commit()

    def _reset_neo4j(self) -> None:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(self.neo4j_uri, auth=self.neo4j_auth)
        try:
            with driver.session(database="neo4j") as session:
                session.run(
                    "MATCH (n) WHERE n:RuleDocument OR n:Chapter OR n:Rule OR n:Topic DETACH DELETE n"
                )
        finally:
            driver.close()

    # ------------------------------------------------------------ 单文档写入
    def _ingest_document(self, document: ParsedDocument) -> dict[str, Any]:
        from app.db.models import Chunk, Document

        point_ids = [str(uuid.uuid4()) for _ in document.chunks]

        # 1) MySQL：documents + chunks
        with new_session(self.mysql_url) as session:
            file_size = Path(document.file_path).stat().st_size if Path(document.file_path).exists() else 0
            record = Document(
                user_id=DEMO_USER_ID,
                title=document.title,
                file_name=document.file_name,
                file_path=document.file_path,
                file_type=document.file_type,
                file_size=file_size,
                chunk_count=len(document.chunks),
                status="done",
                updated_at=datetime.now(),
            )
            session.add(record)
            session.flush()
            document_id = record.id

            for chunk, point_id in zip(document.chunks, point_ids):
                session.add(
                    Chunk(
                        document_id=document_id,
                        qdrant_point_id=point_id,
                        chapter=chunk.chapter,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                    )
                )
            session.commit()

        # 2) Qdrant：向量 + payload（payload 同时用于来源展示与图谱映射）
        from qdrant_client.models import PointStruct

        vectors = self.embedder.encode([chunk.content for chunk in document.chunks])
        points = [
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "document_id": document_id,
                    "document": document.title,
                    "file_name": document.file_name,
                    "chapter": chunk.chapter,
                    "article": chunk.article,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "qdrant_point_id": point_id,
                },
            )
            for chunk, vector, point_id in zip(document.chunks, vectors, point_ids)
        ]
        self._client.upsert(collection_name=self.collection, points=points)

        # 3) Neo4j：文档 → 章节 → 条款 → 主题
        from neo4j import GraphDatabase

        cypher = """
        MERGE (d:RuleDocument {document_id: $document_id})
        SET d.title = $title, d.file_name = $file_name
        WITH d
        UNWIND $chunks AS ch
        MERGE (c:Chapter {document_id: $document_id, title: ch.chapter})
        MERGE (d)-[:HAS_CHAPTER]->(c)
        MERGE (r:Rule {chunk_id: ch.point_id})
        SET r.article = ch.article,
            r.content = ch.content,
            r.qdrant_point_id = ch.point_id,
            r.chunk_index = ch.chunk_index
        MERGE (c)-[:HAS_RULE]->(r)
        WITH r, ch
        UNWIND ch.topics AS topic_name
        MERGE (t:Topic {name: topic_name})
        MERGE (r)-[:ABOUT]->(t)
        """
        chunks_payload = [
            {
                "point_id": point_id,
                "chapter": chunk.chapter,
                "article": chunk.article,
                "content": chunk.content,
                "chunk_index": chunk.chunk_index,
                "topics": classify_topics(chunk.chapter, chunk.content),
            }
            for chunk, point_id in zip(document.chunks, point_ids)
        ]
        driver = GraphDatabase.driver(self.neo4j_uri, auth=self.neo4j_auth)
        try:
            with driver.session(database="neo4j") as session:
                session.run(
                    cypher,
                    document_id=document_id,
                    title=document.title,
                    file_name=document.file_name,
                    chunks=chunks_payload,
                )
        finally:
            driver.close()

        return {
            "document_id": document_id,
            "title": document.title,
            "file_name": document.file_name,
            "chunk_count": len(document.chunks),
            "chapters": len({chunk.chapter for chunk in document.chunks}),
            "point_ids": point_ids,
        }
