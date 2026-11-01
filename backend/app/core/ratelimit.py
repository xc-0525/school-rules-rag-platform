"""进程内限流与并发闸门（P0-3）。

为什么必须有：`/api/v1/chat` 每次至少 2 次大模型调用（工具选择 + 生成），
循环时更多。公网部署且无任何限流时，几个脚本就能把额度刷空。

三层保护：
1. 每分钟请求数（所有 /api 路径，按用户或 IP）；
2. 每日问答数（只对 /chat 计数，按用户）；
3. 单主体并发（同一用户同时只允许 1 个在途问答，避免自打自的刷屏）。

实现说明：状态在进程内。多 worker 部署时每个 worker 各自计数，
实际上限会放大到 N 倍——对"防误用/防脚本"够用；要严格全局限流需把
计数器换到 Redis，接口保持不变即可替换。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """滑动窗口 + 日配额 + 并发计数。"""

    def __init__(
        self,
        per_minute: int = 20,
        per_day: int = 300,
        concurrent: int = 1,
        window_seconds: int = 60,
    ) -> None:
        self.per_minute = max(0, per_minute)
        self.per_day = max(0, per_day)
        self.concurrent = max(0, concurrent)
        self.window = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._daily: dict[str, tuple[str, int]] = {}
        self._inflight: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 分钟级
    def hit(self, key: str) -> tuple[bool, int]:
        """记录一次请求；返回（是否放行, 建议重试秒数）。"""
        if self.per_minute <= 0:
            return True, 0
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= now - self.window:
                bucket.popleft()
            if len(bucket) >= self.per_minute:
                retry_after = max(1, int(bucket[0] + self.window - now) + 1)
                return False, retry_after
            bucket.append(now)
            return True, 0

    # ------------------------------------------------------------------ 日配额
    def hit_daily(self, key: str) -> tuple[bool, int]:
        """记录一次"问答"；按自然日重置。"""
        if self.per_day <= 0:
            return True, 0
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            day, count = self._daily.get(key, (today, 0))
            if day != today:
                day, count = today, 0
            if count >= self.per_day:
                return False, 0
            self._daily[key] = (day, count + 1)
            return True, 0

    # ------------------------------------------------------------------ 并发闸门
    def acquire(self, key: str) -> bool:
        if self.concurrent <= 0:
            return True
        with self._lock:
            if self._inflight[key] >= self.concurrent:
                return False
            self._inflight[key] += 1
            return True

    def release(self, key: str) -> None:
        if self.concurrent <= 0:
            return
        with self._lock:
            current = self._inflight.get(key) or 0
            if current <= 1:
                self._inflight.pop(key, None)
            else:
                self._inflight[key] = current - 1

    def inflight(self, key: str) -> int:
        with self._lock:
            return self._inflight.get(key) or 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._daily.clear()
            self._inflight.clear()
