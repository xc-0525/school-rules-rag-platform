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
                detail=" · ".join(
                    value
                    for value in (hit.payload.get("chapter"), hit.payload.get("article"))
                    if value
                ),
                snippet=hit.payload.get("content", ""),
                score=float(hit.score),
            )
            for hit in hits
        ]


# 查询关键词：来自意图词典 + 查询扩展词典 + 校规高频词（中文小语料用包含匹配最稳）
_GRAPH_TERMS: set[str] = set()
for _keywords in mock_data.INTENT_KEYWORDS.values():
    _GRAPH_TERMS.update(_keywords)
for _synonyms in mock_data.EXPANSIONS.values():
    _GRAPH_TERMS.update(_synonyms)
_GRAPH_TERMS.update(
    {
        "熄灯", "晚归", "夜不归宿", "查寝", "电器", "大功率", "作弊", "手机", "考试",
        "奖学金", "助学金", "励志", "请假", "旷课", "考勤", "打架", "斗殴", "处分",
        "开除", "记过", "留校察看", "论文", "抄袭", "学籍", "注册", "转专业", "休学",
        "退学", "补考", "重修", "评奖", "评优", "宿舍", "宿舍管理", "总则", "附则",
        # 校园服务类（图书馆/食堂/校园卡/网络/医疗/心理/就业/场馆）
        "图书馆", "借阅", "续借", "自习", "食堂", "校园卡", "挂失", "补办",
        "校园网", "网络", "校园码", "校医院", "医疗", "心理咨询", "心理",
        "就业", "招聘", "场馆", "体育", "游泳", "社团", "预约",
    }
)


def extract_graph_keywords(query: str) -> list[str]:
    """从问题中抽取用于图谱检索的关键词（按长度倒序，优先长词）。"""
    matched = [term for term in _GRAPH_TERMS if term in query]
    matched.sort(key=len, reverse=True)
    return list(dict.fromkeys(matched))


class Neo4jGraphStore:
    """Neo4j 图谱检索。

    图谱结构（文档级）：
        (RuleDocument)-[:HAS_CHAPTER]->(Chapter)-[:HAS_RULE]->(Rule)-[:ABOUT]->(Topic)

    检索方式：用问题关键词在 Rule.content / article / Chapter.title 上做包含匹配，
    命中后沿"文档 → 章节 → 条款 → 主题"遍历，返回证据片段（中文小语料下比全文索引更稳）。
    """

    def __init__(
        self,
        uri: str = "",
        user: str = "",
        password: str = "",
        database: str = "neo4j",
    ) -> None:
        self.uri = uri or "bolt://127.0.0.1:7687"
        self.user = user
        self.password = password
        self.database = database

    def _auth(self) -> tuple[str, str] | None:
        return (self.user, self.password) if (self.user or self.password) else None

    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]:
        return await asyncio.to_thread(self._search_sync, query, top_k)

    def _search_sync(self, query: str, top_k: int) -> list[Evidence]:
        keywords = extract_graph_keywords(query)
        if not keywords:
            return []

        from neo4j import GraphDatabase

        cypher = """
        MATCH (d:RuleDocument)-[:HAS_CHAPTER]->(c:Chapter)-[:HAS_RULE]->(r:Rule)
        WHERE any(kw IN $keywords WHERE r.content CONTAINS kw
                  OR r.article CONTAINS kw OR c.title CONTAINS kw)
        OPTIONAL MATCH (r)-[:ABOUT]->(t:Topic)
        WITH d, c, r, collect(DISTINCT t.name) AS topics
        WITH d, c, r, topics,
             size([kw IN $keywords WHERE r.content CONTAINS kw
                   OR r.article CONTAINS kw OR c.title CONTAINS kw]) AS hits
        RETURN d.title AS document, c.title AS chapter, r.article AS article,
               r.content AS content, topics, hits
        ORDER BY hits DESC, document, article
        LIMIT $top_k
        """
        driver = GraphDatabase.driver(self.uri, auth=self._auth())
        try:
            with driver.session(database=self.database) as session:
                rows = session.run(cypher, keywords=keywords, top_k=top_k).data()
        finally:
            driver.close()

        evidence: list[Evidence] = []
        for row in rows:
            topics = "、".join(row.get("topics") or []) or "校规"
            evidence.append(
                Evidence(
                    type="graph",
                    title=f"知识图谱 · {row['document']}",
                    detail=f"{row['chapter']} · {row['article']}（主题：{topics}）",
                    snippet=row["content"],
                    score=min(0.95, 0.55 + 0.1 * int(row.get("hits") or 1)),
                )
            )
        return evidence
