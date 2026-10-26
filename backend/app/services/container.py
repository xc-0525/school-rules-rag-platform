"""服务容器：根据 Settings 组装适配器（Mock / 真实无缝切换）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.services.llm import DeepSeekLLM, FakeLLM
from app.services.persistence import MockPersistence
from app.services.stores import (
    MockGraphStore,
    MockSemanticCache,
    MockVectorStore,
    Neo4jGraphStore,
    QdrantVectorStore,
)


@dataclass
class Services:
    """一次应用运行所需的全部外部依赖。"""

    llm: Any
    vector: Any
    graph: Any
    persistence: Any
    cache: Any


def build_services(settings: Settings) -> Services:
    """按配置选择实现；默认全部 Mock，保证没有外部依赖也能跑通。"""
    llm = FakeLLM() if settings.llm_mode == "fake" else DeepSeekLLM(settings)

    if settings.vector_mode == "qdrant":
        vector: Any = QdrantVectorStore(path=settings.qdrant_path, url=settings.qdrant_url)
    else:
        vector = MockVectorStore()

    if settings.graph_mode == "neo4j":
        graph: Any = Neo4jGraphStore(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
        )
    else:
        graph = MockGraphStore()

    return Services(
        llm=llm,
        vector=vector,
        graph=graph,
        persistence=MockPersistence(),
        cache=MockSemanticCache(),
    )
