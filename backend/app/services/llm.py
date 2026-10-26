"""大模型服务：DeepSeek（真实）与 FakeLLM（测试/离线兜底）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    AsyncOpenAI,
    RateLimitError,
)

from app.core.config import Settings


class LLMError(RuntimeError):
    """大模型调用失败；message 可直接展示给用户。"""


def friendly_llm_error(exc: Exception) -> str:
    """把 SDK 异常翻译成中文提示。"""
    if isinstance(exc, AuthenticationError):
        return "DeepSeek 鉴权失败：API Key 无效或已过期，请检查 backend/.env。"
    if isinstance(exc, RateLimitError):
        return "DeepSeek 调用频率超限或余额不足，请稍后重试。"
    if isinstance(exc, APITimeoutError):
        return "DeepSeek 响应超时，请稍后重试。"
    if isinstance(exc, APIConnectionError):
        return "无法连接 DeepSeek 服务，请检查网络或 DEEPSEEK_BASE_URL。"
    if isinstance(exc, APIStatusError):
        return f"DeepSeek 服务异常（HTTP {exc.status_code}），请稍后重试。"
    return f"大模型调用失败：{type(exc).__name__}: {exc}"


class DeepSeekLLM:
    """基于 OpenAI 兼容接口的 DeepSeek 流式客户端（懒加载，未配置 Key 时不影响启动）。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self.settings.deepseek_api_key:
                raise LLMError(
                    "未配置 DEEPSEEK_API_KEY：请在 backend/.env 中填写后重启服务。"
                )
            self._client = AsyncOpenAI(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.llm_timeout,
            )
        return self._client

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.settings.deepseek_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 统一转成 LLMError 由上层兜底
            raise LLMError(friendly_llm_error(exc)) from exc


DEFAULT_FAKE_ANSWER = (
    "根据《学生宿舍管理规定》第四章 第18条：学生公寓每日 23:30 熄灯并关闭公共区域网络，"
    "次日 06:00 恢复供电。熄灯后请保持安静，不得影响他人休息。"
)


class FakeLLM:
    """确定性假模型：不联网、输出固定文本，用于单测和离线演示。"""

    def __init__(self, answer: str = DEFAULT_FAKE_ANSWER, chunk_size: int = 8) -> None:
        self.answer = answer
        self.chunk_size = chunk_size

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:  # noqa: ARG002
        for start in range(0, len(self.answer), self.chunk_size):
            yield self.answer[start : start + self.chunk_size]
