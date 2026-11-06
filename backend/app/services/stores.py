"""存储适配器：向量库 / 图谱 / 语义缓存。

统一用 Protocol 定义接口，Mock 与真实实现可无缝替换（对接换开关即可）。
Day2 会把 MockVectorStore 换成 Qdrant 本地嵌入式模式，MockGraphStore 换成 Neo4j。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from app.schemas.chat import Evidence, SourceItem
from app.services import lexicon, mock_data

logger = logging.getLogger("app.stores")


class VectorStore(Protocol):
    async def search(self, query: str, top_k: int = 3) -> list[Evidence]: ...


class GraphStore(Protocol):
    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]: ...


class SemanticCache(Protocol):
    def lookup(self, query: str, user_id: int = 0) -> tuple[str, list[SourceItem]] | None: ...

    def store(
        self, query: str, answer: str, sources: list[SourceItem], user_id: int = 0
    ) -> None: ...


def normalize_question(query: str) -> str:
    """语义缓存（当前是精确归一化）用的文本归一化。"""
    drop = set("？?！!。.，,、；;：: 　\n\t")
    return "".join(ch for ch in query.strip() if ch not in drop).lower()

def _cache_key(query: str, user_id: int) -> str:
    """缓存键带用户：工具类回答（我的预约/我的报修）跨用户共享会泄露个人数据。"""
    return f"{int(user_id or 0)}:{normalize_question(query)}"


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


def calibrate_graph_score(
    hits: int, keyword_count: int, min_score: float = 0.30, max_score: float = 0.95
) -> float:
    """把"命中几个关键词"折算成 0~1 的可比分数。

    P1-3 的核心：旧实现 `min(0.95, 0.55 + 0.1*hits)` 只要命中 1 个词就 ≥0.65，
    永远高于向量 cosine 的常见区间，导致"图谱一命中就判证据充分"。
    改成按**关键词覆盖率**线性折算后，只命中 1/4 关键词的条款拿不到高分。
    """
    if keyword_count <= 0 or hits <= 0:
        return 0.0
    coverage = min(1.0, hits / keyword_count)
    return round(min(max_score, min_score + (max_score - min_score) * coverage), 4)


class MockGraphStore:
    """模拟 Neo4j：关键词命中关系路径（分数按覆盖率标定）。"""

    def __init__(self, min_score: float = 0.30, max_score: float = 0.95) -> None:
        self._corpus = mock_data.GRAPH_CORPUS
        self._min_score = min_score
        self._max_score = max_score

    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]:
        hits: list[Evidence] = []
        for item in self._corpus:
            keywords = item["keywords"]
            matched = [keyword for keyword in keywords if keyword in query]
            if not matched:
                continue
            hits.append(
                Evidence(
                    type="graph",
                    title="知识图谱关系路径",
                    detail=item["path_id"],
                    snippet=item["content"],
                    score=calibrate_graph_score(
                        len(matched), len(keywords), self._min_score, self._max_score
                    ),
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]


class MockSemanticCache:
    """问题归一化缓存（精确匹配，非向量语义缓存）。

    P1-8 修复：
    - 加 TTL：文档更新后即使忘了 clear，也不会长期返回旧答案；
    - 加容量上限：防止长期运行内存无限增长；
    - 命中/写入策略在 nodes.py 里进一步收紧（指代型追问不查缓存、
      多轮会话的答案不写全局缓存），避免"同一个短问题在不同上下文串味"。

    说明：当前只是把问题去掉标点/空格/转小写后做 dict 精确查找，
    答辩时建议称呼为"问题归一化缓存"；真正的语义缓存需要 embedding 相似度。
    """

    def __init__(self, ttl_seconds: int = 1800, max_entries: int = 1000) -> None:
        self._ttl = max(0, int(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._data: dict[str, tuple[float, str, list[SourceItem]]] = {}
        now = time.time()
        for question, value in mock_data.CACHE_SEED.items():
            self.store(question, value["answer"], [SourceItem(**source) for source in value["sources"]])
        self._now = now

    def lookup(self, query: str, user_id: int = 0) -> tuple[str, list[SourceItem]] | None:
        key = _cache_key(query, user_id)
        row = self._data.get(key)
        if row is None:
            return None
        expires_at, answer, sources = row
        if expires_at <= time.time():
            self._data.pop(key, None)
            return None
        return answer, list(sources)

    def store(
        self, query: str, answer: str, sources: list[SourceItem], user_id: int = 0
    ) -> None:
        if not answer:
            return
        if len(self._data) >= self._max_entries:
            self._evict()
        expires_at = time.time() + self._ttl if self._ttl else float("inf")
        self._data[_cache_key(query, user_id)] = (expires_at, answer, list(sources))

    def _evict(self) -> None:
        """先清过期，再淘汰最接近过期的一条。"""
        now = time.time()
        for key in [k for k, row in self._data.items() if row[0] <= now]:
            self._data.pop(key, None)
        if len(self._data) >= self._max_entries and self._data:
            oldest = min(self._data.items(), key=lambda item: item[1][0])[0]
            self._data.pop(oldest, None)

    def clear(self) -> None:
        """清空缓存（文档上传/删除后调用，避免旧答案/旧来源继续命中）。"""
        self._data.clear()


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
        # 编码是 CPU 密集的，单独用信号量限并发；不再串在 Qdrant 客户端的锁上
        self._encode_sem = asyncio.Semaphore(4)

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
        # 只有"初始化"和"客户端调用"需要加锁；编码放到锁外，
        # 否则所有并发用户的检索会被 BGE 编码串成一条队列（P2-1）。
        async with self._lock:
            await asyncio.to_thread(self.ensure_ready)
            client = self._client
        async with self._encode_sem:
            vector = await asyncio.to_thread(self.embedder.encode_one, query)
        async with self._lock:
            hits = await asyncio.to_thread(
                lambda: client.query_points(
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

    def close(self) -> None:
        """关闭本地 Qdrant 客户端（应用退出时调用）。"""
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None


# 查询关键词：来自领域词典（app/services/lexicon.py），不是 mock 数据
_GRAPH_TERMS: set[str] = set(lexicon.GRAPH_TERMS)


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
        min_score: float = 0.30,
        max_score: float = 0.95,
        min_coverage: float = 0.34,
    ) -> None:
        self.uri = uri or "bolt://127.0.0.1:7687"
        self.user = user
        self.password = password
        self.database = database
        self._min_score = min_score
        self._max_score = max_score
        self._min_coverage = min_coverage
        self._driver: Any = None

    def _auth(self) -> tuple[str, str] | None:
        return (self.user, self.password) if (self.user or self.password) else None

    def _get_driver(self) -> Any:
        """懒加载并复用 driver（避免每次检索都新建/关闭连接）。"""
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(self.uri, auth=self._auth())
        return self._driver

    def close(self) -> None:
        """应用退出时关闭 driver。"""
        if self._driver is not None:
            try:
                self._driver.close()
            finally:
                self._driver = None

    @staticmethod
    def _expand_by_topic(session: Any, rows: list[dict], limit: int) -> list[dict]:
        """沿共享 Topic 找相邻条款（一跳）。

        只在直接命中不足 top_k 时触发；返回的行 `hits` 按 1 计、并带 hop=2，
        打分时会被折价，避免"邻居"顶掉真正命中的条款。
        """
        chunk_ids = [row.get("chunk_id") for row in rows if row.get("chunk_id")]
        if not chunk_ids or limit <= 0:
            return []
        cypher = """
        MATCH (n:Rule)-[:ABOUT]->(t:Topic)<-[:ABOUT]-(r:Rule)
        WHERE r.chunk_id IN $chunk_ids AND n.chunk_id <> r.chunk_id
        MATCH (d:RuleDocument)-[:HAS_CHAPTER]->(c:Chapter)-[:HAS_RULE]->(n)
        RETURN DISTINCT d.title AS document, c.title AS chapter, n.article AS article,
               n.content AS content, collect(DISTINCT t.name) AS topics,
               1 AS hits, 2 AS hop, n.chunk_id AS chunk_id
        LIMIT $limit
        """
        return session.run(cypher, chunk_ids=chunk_ids, limit=limit).data()

    async def search_paths(self, query: str, top_k: int = 2) -> list[Evidence]:
        return await asyncio.to_thread(self._search_sync, query, top_k)

    def _search_sync(self, query: str, top_k: int) -> list[Evidence]:
        keywords = extract_graph_keywords(query)
        if not keywords:
            return []

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
               r.content AS content, topics, hits, r.chunk_id AS chunk_id, 1 AS hop
        ORDER BY hits DESC, document, article
        LIMIT $top_k
        """
        driver = self._get_driver()
        with driver.session(database=self.database) as session:
            rows = session.run(cypher, keywords=keywords, top_k=top_k).data()
            # 命中不足时做一次"条款 → 主题 → 相邻条款"的扩展（真一跳关系）。
            # 论文里如实描述为"主题关联扩展"，不要写成"多跳推理"。
            if rows and len(rows) < top_k:
                try:
                    rows.extend(self._expand_by_topic(session, rows, top_k - len(rows)))
                except Exception:  # noqa: BLE001 —— 扩展失败不影响主检索
                    logger.exception("图谱主题扩展失败，返回直接命中结果")

        evidence: list[Evidence] = []
        keyword_count = max(1, len(keywords))
        for row in rows:
            hits = int(row.get("hits") or 0)
            coverage = hits / keyword_count
            if coverage < self._min_coverage:
                continue  # 只蹭到一个泛词，不足以作为证据
            topics = "、".join(row.get("topics") or []) or "校规"
            hop = int(row.get("hop") or 1)
            score = calibrate_graph_score(
                hits, keyword_count, self._min_score, self._max_score
            )
            if hop > 1:
                score = round(score * 0.85, 4)  # 邻居证据折价
                topics = f"{topics}；经主题关联扩展"
            evidence.append(
                Evidence(
                    type="graph",
                    title=f"知识图谱 · {row['document']}",
                    detail=f"{row['chapter']} · {row['article']}（主题：{topics}）",
                    snippet=row["content"],
                    score=score,
                )
            )
        return evidence
