"""本地 BGE 向量模型（sentence-transformers）。

复用 G:\ai-models 下已缓存的 bge-small-zh-v1.5，零下载。
模型加载较慢（首次约 1~2 分钟，取决于磁盘/杀软），所以：
- 应用启动时预热（见 app/main.py 的 lifespan）；
- 查询时用 to_thread 执行，避免阻塞事件循环。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class BGEEmbedder:
    """BAAI/bge-small-zh-v1.5 中文向量模型（512 维）。"""

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self._model: Any = None
        self._lock = threading.Lock()

    def ensure_loaded(self) -> None:
        """懒加载：第一次调用时把模型读进内存（线程安全）。"""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            path = Path(self.model_path)
            if not path.exists():
                raise RuntimeError(
                    f"找不到本地 BGE 模型：{self.model_path}。"
                    "请检查 backend/.env 的 EMBEDDING_MODEL_PATH。"
                )
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(str(path))

    def encode(self, texts: list[str], normalize: bool = True) -> list[list[float]]:
        self.ensure_loaded()
        vectors = self._model.encode(
            texts,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in vectors.tolist()]

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]
