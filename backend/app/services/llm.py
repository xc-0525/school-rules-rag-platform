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

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """非流式 function calling：返回 content + 规范化后的 tool_calls。"""
        try:
            response = await self.client.chat.completions.create(
                model=self.settings.deepseek_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=self.settings.llm_temperature,
            )
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMError(friendly_llm_error(exc)) from exc

        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            calls.append(
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                }
            )
        return {"content": message.content or "", "tool_calls": calls}

    async def rewrite_query(
        self, history: list[dict[str, str]], query: str
    ) -> str:
        """多轮追问改写为可独立检索的问题（不依赖工具）。"""
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是校规问答系统的查询改写器。根据对话历史，把用户的追问改写成"
                    "一个不依赖上下文、可独立检索的完整问题；只输出改写后的问句，不要解释。"
                ),
            }
        ]
        messages.extend(history[-6:])
        messages.append({"role": "user", "content": query})
        try:
            response = await self.client.chat.completions.create(
                model=self.settings.deepseek_model,
                messages=messages,
                temperature=0.0,
            )
            content = (response.choices[0].message.content or "").strip()
            return content or query
        except Exception:  # noqa: BLE001 —— 改写失败不阻断主流程
            return query


    async def judge_sufficiency(
        self, question: str, evidence: list[dict[str, str]]
    ) -> dict[str, Any]:
        """判断召回条款能否回答该问题（拒答判定的核心一步）。

        为什么需要它：真实语料实测定标发现，可答题最低向量分 0.567、
        应拒答题最高 0.620，两者区间重叠——只靠相似度阈值必然同时误杀和漏放。
        这一步补的是"条款内容是否真的回答了问题"，而不只是"语义是否相近"。

        返回 {"sufficient": bool, "reason": str}；任何异常都返回放行，
        避免判定环节故障导致整站拒答。
        """
        if not evidence:
            return {"sufficient": False, "reason": "未召回任何条款"}

        blocks = []
        for index, item in enumerate(evidence[:4], start=1):
            text = (item.get("snippet") or "").strip().replace("\n", " ")
            blocks.append(
                f"[{index}] {item.get('detail') or item.get('title') or ''}\n{text[:280]}"
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是校规问答系统的证据审核员。判断下面这些条款能否回答学生的问题。\n"
                    "判定标准：\n"
                    "1. 只有条款内容**直接回答了问题**才算 sufficient；\n"
                    "2. 只是出现相关词汇、但没有给出答案的，判 insufficient；\n"
                    "3. 问题涉及的主体（如食堂、校医院、校车、快递、停车）在条款中"
                    "完全没有出现的，判 insufficient；\n"
                    "4. 不要用你自己的知识补充，只依据给出的条款。\n"
                    '只输出 JSON：{"sufficient": true 或 false, "reason": "一句话理由"}'
                ),
            },
            {
                "role": "user",
                "content": f"问题：{question}\n\n召回条款：\n" + "\n\n".join(blocks),
            },
        ]
        try:
            response = await self.client.chat.completions.create(
                model=self.settings.deepseek_model,
                messages=messages,
                temperature=0.0,
            )
            content = (response.choices[0].message.content or "").strip()
            data = _extract_json(content)
            if isinstance(data.get("sufficient"), bool):
                return {
                    "sufficient": bool(data["sufficient"]),
                    "reason": str(data.get("reason") or "")[:120],
                }
            return {"sufficient": True, "reason": "判定结果无法解析，按召回结果放行"}
        except Exception as exc:  # noqa: BLE001 —— 判定失败不能阻断问答
            return {"sufficient": True, "reason": f"判定调用失败（{type(exc).__name__}），放行"}


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出里取第一个 JSON 对象（容忍 ```json 包裹与前后废话）。"""
    import json
    import re

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.S)
        candidate = brace.group(0) if brace else "{}"
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


DEFAULT_FAKE_ANSWER = (
    "根据《学生宿舍管理规定》第四章 第18条：学生公寓每日 23:30 熄灯并关闭公共区域网络，"
    "次日 06:00 恢复供电。熄灯后请保持安静，不得影响他人休息。"
)


class FakeLLM:
    """确定性假模型：不联网、输出固定文本，用于单测和离线演示。"""

    def __init__(
        self,
        answer: str = DEFAULT_FAKE_ANSWER,
        chunk_size: int = 8,
        sufficiency: bool = True,
        sufficiency_reason: str = "假模型默认放行",
    ) -> None:
        self.answer = answer
        self.chunk_size = chunk_size
        self.sufficiency = sufficiency
        self.sufficiency_reason = sufficiency_reason

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:  # noqa: ARG002
        for start in range(0, len(self.answer), self.chunk_size):
            yield self.answer[start : start + self.chunk_size]

    async def chat_with_tools(  # noqa: ARG002
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """离线假模型不主动选工具，保证无网络时仍走确定性 RAG 链路。"""
        return {"content": "", "tool_calls": []}

    async def rewrite_query(self, history: list[dict[str, str]], query: str) -> str:  # noqa: ARG002
        return query

    async def judge_sufficiency(  # noqa: ARG002
        self, question: str, evidence: list[dict[str, str]]
    ) -> dict[str, Any]:
        """离线假模型的判定结果可配置，便于单测覆盖"判足/判不足"两条路径。"""
        return {"sufficient": self.sufficiency, "reason": self.sufficiency_reason}
