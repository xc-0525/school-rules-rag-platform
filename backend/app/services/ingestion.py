"""校规文档入库：一次写入 MySQL（元数据）+ Qdrant（向量）+ Neo4j（图谱）。

用法（见 backend/scripts/ingest_rules.py）：
    python scripts/ingest_rules.py --dir ../rules --reset

流程：
1. rules_parser 解析目录下的 .md/.txt → 章节 + 条款 chunks；
2. MySQL documents / chunks 记录（chunks.qdrant_point_id 建立溯源映射）；
3. BGE 向量化 → 写入 Qdrant（本地嵌入式）；
4. 依据"文档 → 章节 → 条款 → 主题"关系写入 Neo4j 图谱。
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

    def __init__(self, settings: Settings) -> None:
        if not settings.embedding_model_path:
            raise RuntimeError("未配置 EMBEDDING_MODEL_PATH（指向本机 bge-small-zh-v1.5 目录）")
        self.settings = settings
        self.embedder = BGEEmbedder(settings.embedding_model_path)
        self.qdrant_path = _resolve_path(settings.qdrant_path, "data/qdrant")
        self.qdrant_collection = settings.qdrant_collection
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
        documents = load_documents(directory)
        if not documents:
            raise RuntimeError(f"目录中没有可解析的 .md/.txt 文档：{directory}")

        self._prepare_qdrant(reset)
        if reset:
            self._reset_mysql()
            self._reset_neo4j()

        result = IngestResult(titles=[])
        for document in documents:
            counts = self._ingest_document(document)
            result.documents += 1
            result.chunks += counts["chunks"]
            result.qdrant_points += counts["chunks"]
            result.neo4j_rules += counts["chunks"]
            assert result.titles is not None
            result.titles.append(document.title)
        return result

    # ------------------------------------------------------------ Qdrant
    def _prepare_qdrant(self, reset: bool) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.embedder.ensure_loaded()
        self._client = QdrantClient(path=self.qdrant_path)
        existing = {item.name for item in self._client.get_collections().collections}

        if reset and self.qdrant_collection in existing:
            self._client.delete_collection(self.qdrant_collection)
            existing.discard(self.qdrant_collection)

        if self.qdrant_collection not in existing:
            dimension = len(self.embedder.encode_one("测试"))
            self._client.create_collection(
                collection_name=self.qdrant_collection,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )

    # ------------------------------------------------------------ MySQL
    def _reset_mysql(self) -> None:
        from sqlalchemy import text

        with new_session(self.mysql_url) as session:
            session.execute(text("DELETE FROM documents"))
            session.commit()

    # ------------------------------------------------------------ Neo4j
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

    # ------------------------------------------------------------ 单文档流程
    def _ingest_document(self, document: ParsedDocument) -> dict[str, int]:
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
        self._client.upsert(collection_name=self.qdrant_collection, points=points)

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

        return {"chunks": len(document.chunks)}
