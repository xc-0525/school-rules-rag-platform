"""存储适配器：向量库 / 图谱 / 语义缓存。

统一用 Protocol 定义接口，Mock 与真实实现可无缝替换（对接换开关即可）。
Day2 会把 MockVectorStore 换成 Qdrant 本地嵌入式模式，MockGraphStore 换成 Neo4j。
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from app.schemas.chat import Evidence, SourceItem
from app.services import mock_data


class VectorStore(Protocol):
    async def search(self, query: str, top_k: int = 3) -> list[Evidence]: ...


class GraphStore(Protocol):
    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]: ...


class SemanticCache(Protocol):
    def lookup(self, query: str) -> tuple[str, list[SourceItem]] | None: ...

    def store(self, query: str, answer: str, sources: list[SourceItem]) -> None: ...


def normalize_question(query: str) -> str:
    """语义缓存（当前是精确归一化）用的文本归一化。"""
    drop = set("？?！!。.，,、；;：: 　\n\t")
    return "".join(ch for ch in query.strip() if ch not in drop).lower()


class MockVectorStore:
    """模拟 Qdrant：关键词命中 + 相似度排序。"""

    def __init__(self) -> None:
        self._corpus = mock_data.VECTOR_CORPUS

    async def search(self, query: str, top_k: int = 3) -> list[Evidence]:
        hits: list[Evidence] = []
        for item in self._corpus:
            if any(keyword in query for keyword in item["keywords"]):
                hits.append(
                    Evidence(
                        type="vector",
                        title=item["document"],
                        detail=item["chapter"],
                        snippet=item["content"],
                        score=item["score"],
                    )
                )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]


class MockGraphStore:
    """模拟 Neo4j：关键词命中关系路径。"""

    def __init__(self) -> None:
        self._corpus = mock_data.GRAPH_CORPUS

    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]:
        hits: list[Evidence] = []
        for item in self._corpus:
            if any(keyword in query for keyword in item["keywords"]):
                hits.append(
                    Evidence(
                        type="graph",
                        title="知识图谱关系路径",
                        detail=item["path_id"],
                        snippet=item["content"],
                        score=item["score"],
                    )
                )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]


class MockSemanticCache:
    """进程内语义缓存（Day2 换成 MySQL + Qdrant 实现）。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, list[SourceItem]]] = {
            normalize_question(question): (
                value["answer"],
                [SourceItem(**source) for source in value["sources"]],
            )
            for question, value in mock_data.CACHE_SEED.items()
        }

    def lookup(self, query: str) -> tuple[str, list[SourceItem]] | None:
        return self._data.get(normalize_question(query))

    def store(self, query: str, answer: str, sources: list[SourceItem]) -> None:
        if not answer:
            return
        self._data[normalize_question(query)] = (answer, list(sources))


# ---------------------------------------------------------------------------
# 真实实现：Qdrant 本地嵌入式模式（不装 Docker，复用本机已有的 BGE 模型）
# ---------------------------------------------------------------------------
class QdrantVectorStore:
    """真实 Qdrant 向量库。

    - 本地嵌入式模式：QdrantClient(path=...)，数据落在 backend/data/qdrant；
    - 也支持服务模式：QdrantClient(url=...)，后续换服务器只改 .env；
    - 首次启动自动建集合 + 把 mock_data 里的校规语料灌进去（种子库）；
      后续 Day4 接入真实文档入库时，换成 ingestion 脚本写同一集合。
    """

    def __init__(
        self,
        path: str = "",
        url: str = "",
        collection: str = "rules_chunks",
        embedder: Any = None,
        corpus: list[dict[str, Any]] | None = None,
    ) -> None:
        self.path = path
        self.url = url
        self.collection = collection
        self.embedder = embedder
        self.corpus = corpus or mock_data.VECTOR_CORPUS
        self._client: Any = None
        self._lock = asyncio.Lock()

    # ---------------- 初始化 / 种子数据 ----------------
    def ensure_ready(self) -> None:
        """同步初始化（可在线程池里调用；也可在应用启动时预热）。"""
        if self._client is not None:
            return
        from qdrant_client import QdrantClient

        self.embedder.ensure_loaded()
        client = QdrantClient(path=self.path) if self.path else QdrantClient(url=self.url)
        self._client = client

        existing = {item.name for item in client.get_collections().collections}
        if self.collection not in existing:
            self._create_and_seed(client)

    def _create_and_seed(self, client: Any) -> None:
        from qdrant_client.models import Distance, PointStruct, VectorParams

        sample = self.embedder.encode_one("测试")
        client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=len(sample), distance=Distance.COSINE),
        )
        texts = [item["content"] for item in self.corpus]
        vectors = self.embedder.encode(texts)
        points = [
            PointStruct(
                id=index,
                vector=vector,
                payload={
                    "chunk_id": item["chunk_id"],
                    "document": item["document"],
                    "chapter": item["chapter"],
                    "content": item["content"],
                    "base_score": item["score"],
                },
            )
            for index, (item, vector) in enumerate(zip(self.corpus, vectors), start=1)
        ]
        client.upsert(collection_name=self.collection, points=points)

    # ---------------- 检索 ----------------
    async def search(self, query: str, top_k: int = 3) -> list[Evidence]:
        async with self._lock:
            await asyncio.to_thread(self.ensure_ready)
            vector = await asyncio.to_thread(self.embedder.encode_one, query)
            hits = await asyncio.to_thread(
                lambda: self._client.query_points(
                    collection_name=self.collection,
                    query=vector,
                    limit=top_k,
                    with_payload=True,
                ).points
            )
        return [
            Evidence(
                type="vector",
                title=hit.payload.get("document", ""),
                detail=hit.payload.get("chapter", ""),
                snippet=hit.payload.get("content", ""),
                score=float(hit.score),
            )
            for hit in hits
        ]


class Neo4jGraphStore:
    """Neo4j 图谱检索。Day5 实现。"""

    def __init__(self, uri: str = "", user: str = "neo4j", password: str = "") -> None:
        self.uri = uri
        self.user = user
        self.password = password

    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]:
        raise NotImplementedError("Neo4j 适配器将在 Day5 接入")
