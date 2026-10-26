"""存储适配器：向量库 / 图谱 / 语义缓存。

统一用 Protocol 定义接口，Mock 与真实实现可无缝替换（对接换开关即可）。
Day2 会把 MockVectorStore 换成 Qdrant 本地嵌入式模式，MockGraphStore 换成 Neo4j。
"""

from __future__ import annotations

from typing import Protocol

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
# 真实实现占位：Day2 / Day5 接入，检索节点不用改
# ---------------------------------------------------------------------------
class QdrantVectorStore:
    """Qdrant 向量库（本地嵌入式模式 / 服务模式）。Day2 实现。"""

    def __init__(self, path: str = "", url: str = "", collection: str = "rules_chunks") -> None:
        self.path = path
        self.url = url
        self.collection = collection

    async def search(self, query: str, top_k: int = 3) -> list[Evidence]:
        raise NotImplementedError("Qdrant 适配器将在 Day2 接入")


class Neo4jGraphStore:
    """Neo4j 图谱检索。Day5 实现。"""

    def __init__(self, uri: str = "", user: str = "neo4j", password: str = "") -> None:
        self.uri = uri
        self.user = user
        self.password = password

    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]:
        raise NotImplementedError("Neo4j 适配器将在 Day5 接入")
