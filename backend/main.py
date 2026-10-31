"""
============================================================================
 毕业设计《基于 FastAPI 和 Vue 的校园服务智能问答系统的设计与实现》
 极简版主循环（MVP Demo）—— 今晚即可跑通的最小闭环
============================================================================
 一条请求的完整链路：

    用户 query
      ├─ 检索 Agent（Mock：模拟 Qdrant 向量语义召回）──┐
      └─ 图谱 Agent（Mock：模拟 Neo4j 多跳关系推理）──┤ 两个 Agent 并行执行
                                                     ↓
                                多路证据融合（把两路结果拼成【背景资料】）
                                                     ↓
                   防幻觉校验：证据为空 → 直接拒答（不浪费一次大模型调用）
                                                     ↓
                      DeepSeek 证据约束生成（真实调用 · Streaming 流式输出）
                                                     ↓
                       MySQL 落库（Mock：print 模拟，暂不接 SQLAlchemy）
                                                     ↓
                          返回最终答案 + 条款来源引用

 启动方式：
   cd backend
   pip install -r requirements.txt
   copy .env.example .env        # Windows；Linux/macOS 用 cp
   # 编辑 .env，填入 DEEPSEEK_API_KEY
   uvicorn main:app --reload --port 8000

 打开：
   http://127.0.0.1:8000/docs    Swagger UI（推荐把 stream 设为 false 调试）
   http://127.0.0.1:8000/        一个极简网页，可直观看到“打字机式”流式输出
============================================================================
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, Field

# ============================================================================
# 一、配置加载
#   用 python-dotenv 读取 backend/.env，避免把密钥硬编码进代码。
#   load_dotenv 默认不覆盖已存在的系统环境变量，所以 CI / 服务器上直接注入
#   环境变量也能生效。
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# DeepSeek 相关配置（.env 中配置）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

# 两个 Agent 的模拟耗时（秒），用来演示“并行执行”带来的收益：
# 串行大约 0.30s，用 asyncio.gather 并行后大约 0.15s。
AGENT_MOCK_LATENCY = 0.15

# 防幻觉兜底话术：证据为空时直接返回，不调用大模型
NO_EVIDENCE_ANSWER = (
    "抱歉，未找到相关校规或校园服务信息。建议换一种说法重新提问，或联系辅导员 / 学生工作处进行人工确认。"
)

# ============================================================================
# 二、FastAPI 应用实例
# ============================================================================

app = FastAPI(
    title="校园服务智能问答系统 · 极简版主循环",
    version="0.1.0",
    description=(
        "**多智能体 + RAG 最小闭环 Demo**\n\n"
        "请求链路：检索 Agent（Mock Qdrant）与图谱 Agent（Mock Neo4j）并行召回 "
        "→ 证据融合 → 防幻觉校验 → DeepSeek 流式生成 → MySQL 落库（Mock）。\n\n"
        "### SSE 流式事件格式（`stream=true` 时）\n"
        "- `event: meta` → `{\"evidence_found\": true, \"sources\": [...]}`\n"
        "- `event: delta` → `{\"content\": \"逐字增量\"}`\n"
        "- `event: error` → `{\"message\": \"错误说明\"}`（终止事件）\n"
        "- `event: done` → `{\"answer\": \"完整答案\", \"latency_ms\": 1200}`\n\n"
        "> 在 Swagger 里调试时，建议先把 `stream` 设为 `false`，可以直接看到结构化 JSON。"
    ),
)


# ============================================================================
# 三、Pydantic 请求 / 响应模型
# ============================================================================

class ChatRequest(BaseModel):
    """问答请求体"""

    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="学生的问题（1~500 字）",
        examples=["宿舍晚上几点熄灯？"],
    )
    stream: bool = Field(
        default=True,
        description="True：SSE 流式返回（默认，技术亮点）；False：一次性返回 JSON（Swagger 调试更方便）",
    )


class SourceItem(BaseModel):
    """一条证据来源，用于前端展示“答案出自哪条规定”"""

    type: Literal["vector", "graph"] = Field(description="证据类型：vector=向量召回，graph=图谱关系")
    title: str = Field(description="来源名称，如《学生宿舍管理规定》")
    detail: str = Field(description="章节 / 关系路径，如“第四章 第18条”")
    snippet: str = Field(description="证据原文片段")


class ChatResponse(BaseModel):
    """非流式返回值（stream=false 时使用）"""

    query: str = Field(description="原始问题")
    answer: str = Field(description="最终答案（无证据时为固定的拒答话术）")
    sources: list[SourceItem] = Field(default_factory=list, description="证据来源列表")
    evidence_found: bool = Field(description="是否召回证据；False 表示触发了防幻觉拒答")
    latency_ms: int = Field(description="服务端总耗时（毫秒）")


# ============================================================================
# 四、Mock 数据：模拟 Qdrant 向量库 + Neo4j 知识图谱
#   真实版本里，这两份数据分别来自 Qdrant 的向量召回和 Neo4j 的 Cypher 查询；
#   现在用内存常量代替，保证不装数据库也能演示完整链路。
# ============================================================================

# 模拟 Qdrant 的分块召回结果（一条 = 一个文本分块）
MOCK_QDRANT_CHUNKS: list[dict[str, Any]] = [
    {
        "chunk_id": "chunk-1001",
        "document": "《学生宿舍管理规定》",
        "chapter": "第四章 第18条",
        "content": (
            "学生公寓实行统一熄灯制度，每日 23:30 熄灯并关闭公共区域网络，"
            "次日 06:00 恢复供电。熄灯后不得在楼道、宿舍内大声喧哗影响他人休息。"
        ),
        "score": 0.93,
        "keywords": ["熄灯", "宿舍", "断电"],
    },
    {
        "chunk_id": "chunk-1002",
        "document": "《学生宿舍管理规定》",
        "chapter": "第四章 第19条",
        "content": (
            "学生应按时归宿，23:30 后归寝须在楼管处登记晚归原因；"
            "未经批准夜不归宿的，给予通报批评，一学期累计三次以上给予警告处分。"
        ),
        "score": 0.90,
        "keywords": ["晚归", "夜不归宿", "宿舍"],
    },
    {
        "chunk_id": "chunk-1003",
        "document": "《学生宿舍管理规定》",
        "chapter": "第五章 第22条",
        "content": (
            "宿舍内严禁使用热得快、电炉、电饭锅等大功率电器（单件功率超过 800W），"
            "违者由后勤部门暂扣器具，并视情节给予警告以上处分。"
        ),
        "score": 0.88,
        "keywords": ["电器", "大功率", "宿舍"],
    },
    {
        "chunk_id": "chunk-2001",
        "document": "《学生考试纪律与违纪处理办法》",
        "chapter": "第二章 第5条",
        "content": (
            "考试过程中携带手机、智能手表等电子设备进入考场的，无论是否使用，"
            "均按考试违纪处理，该科成绩记为无效，并给予记过处分。"
        ),
        "score": 0.94,
        "keywords": ["作弊", "考试", "手机", "违纪"],
    },
    {
        "chunk_id": "chunk-3001",
        "document": "《学生奖学金评定办法》",
        "chapter": "第三章 第9条",
        "content": (
            "国家奖学金评定要求：学年学业成绩排名位于本专业前 10%，"
            "综合素质测评位于前 20%，且当学年无任何违纪处分记录。"
        ),
        "score": 0.91,
        "keywords": ["奖学金", "国家奖学金", "助学金"],
    },
    {
        "chunk_id": "chunk-4001",
        "document": "《学生考勤管理办法》",
        "chapter": "第二章 第7条",
        "content": (
            "学生请假须提前在教务系统提交申请：三天以内由辅导员审批，"
            "三天以上至两周由学院分管领导审批，两周以上报学生工作处备案。"
        ),
        "score": 0.89,
        "keywords": ["请假", "旷课", "考勤"],
    },
    {
        "chunk_id": "chunk-5001",
        "document": "《学生违纪处分条例》",
        "chapter": "第三章 第12条",
        "content": (
            "打架斗殴的，视情节轻重给予警告至留校察看处分；"
            "持械斗殴、结伙斗殴或造成他人人身伤害的，给予开除学籍处分。"
        ),
        "score": 0.87,
        "keywords": ["打架", "斗殴", "处分"],
    },
]

# 模拟 Neo4j 的多跳关系路径（一条 = 一条图谱路径）
MOCK_NEO4J_PATHS: list[dict[str, Any]] = [
    {
        "path_id": "path-201",
        "content": (
            "(学生)-[:违反]->(宿舍管理规定)-[:对应处分]->(警告/严重警告)"
            "-[:记入]->(学生综合素质测评)"
        ),
        "score": 0.86,
        "keywords": ["熄灯", "晚归", "夜不归宿", "宿舍", "电器"],
    },
    {
        "path_id": "path-202",
        "content": (
            "(考试作弊)-[:属于]->(违纪行为)-[:适用条款]->(《考试纪律与违纪处理办法》第5条)"
            "-[:处分等级]->(记过)-[:影响]->(评奖评优资格)"
        ),
        "score": 0.92,
        "keywords": ["作弊", "考试", "违纪"],
    },
    {
        "path_id": "path-203",
        "content": (
            "(国家奖学金)-[:要求]->(学业成绩前10%)-[:并且]->(无违纪记录)"
            "-[:评定单位]->(学生工作处)"
        ),
        "score": 0.85,
        "keywords": ["奖学金", "助学金"],
    },
    {
        "path_id": "path-204",
        "content": (
            "(请假申请)-[:流程]->(辅导员审批)-[:超过3天]->(学院分管领导审批)"
            "-[:超过2周]->(学生工作处备案)"
        ),
        "score": 0.84,
        "keywords": ["请假", "考勤"],
    },
]


# ============================================================================
# 五、三个 Agent（前两个是 Mock，第三个真实调用 DeepSeek）
# ============================================================================

def _match_chunks(query: str) -> list[dict[str, Any]]:
    """极简关键词匹配：找出 query 命中的 Mock 分块（模拟向量语义召回的 Top-K）。"""
    hits: list[dict[str, Any]] = []
    for chunk in MOCK_QDRANT_CHUNKS:
        # 任意一个关键词出现在 query 里，就认为该分块被召回
        if any(keyword in query for keyword in chunk["keywords"]):
            hits.append(chunk)
    # 按相似度倒序，取 Top-3，模拟 Qdrant 的 ANN 检索 + score 排序
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits[:3]


async def agent_retrieve(query: str) -> list[dict[str, Any]]:
    """
    检索 Agent（Mock）
    ------------------------------------------------------------------
    真实实现：query → BGE-small-zh 向量化 → Qdrant 向量检索 Top-K 分块
              → 用 chunks.qdrant_point_id 回查 MySQL，补齐文档/章节信息。
    当前实现：关键词匹配内存中的 Mock 分块，并用 asyncio.sleep 模拟检索耗时。
    返回：分块列表（含 chunk_id / document / chapter / content / score）
    """
    await asyncio.sleep(AGENT_MOCK_LATENCY)
    return _match_chunks(query)


async def agent_graph(query: str) -> list[dict[str, Any]]:
    """
    图谱 Agent（Mock）
    ------------------------------------------------------------------
    真实实现：query → 实体识别 → Neo4j Cypher 多跳查询
              （MATCH (n)-[r*1..3]->(m) ...），返回实体与关系路径。
    当前实现：关键词匹配内存中的 Mock 路径，并用 asyncio.sleep 模拟查询耗时。
    返回：关系路径列表（含 path_id / content / score）
    """
    await asyncio.sleep(AGENT_MOCK_LATENCY)
    paths = [
        path
        for path in MOCK_NEO4J_PATHS
        if any(keyword in query for keyword in path["keywords"])
    ]
    paths.sort(key=lambda item: item["score"], reverse=True)
    return paths[:2]


async def agent_generate(query: str, evidence: str) -> AsyncIterator[str]:
    """
    生成 Agent（真实调用 DeepSeek，流式输出）
    ------------------------------------------------------------------
    入参：
      query    —— 用户原始问题
      evidence —— 检索 Agent + 图谱 Agent 融合后的【背景资料】
    做法：
      1. 组装“证据约束 Prompt”（System 里明确禁止编造，只许依据资料作答）；
      2. 调用 DeepSeek（OpenAI 兼容接口），stream=True 逐字产出；
      3. 以异步生成器形式 yield 每个 token，供上层转成 SSE 推给前端。
    异常：
      网络 / 鉴权 / 限流等异常不在这里吞掉，交由调用方统一转换成人话错误信息。
    """
    client = get_llm_client()
    stream = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=build_messages(query, evidence),
        temperature=LLM_TEMPERATURE,
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        # delta.content 即本次增量文本（可能为 None，需要判空）
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# ============================================================================
# 六、证据融合（把两个 Agent 的结果拼成一段【背景资料】）
# ============================================================================

def fuse_evidence(
    vector_hits: list[dict[str, Any]],
    graph_paths: list[dict[str, Any]],
) -> str:
    """
    多路证据融合
    ------------------------------------------------------------------
    - 向量召回的每个分块 → 一条带出处的证据（文档 / 章节 / 分块 ID / 相似度）；
    - 图谱路径 → 单独打上“图谱关系”标签；
    - 两路都为空时返回空字符串，交由上层触发防幻觉拒答。
    """
    blocks: list[str] = []

    for index, hit in enumerate(vector_hits, start=1):
        blocks.append(
            f"[向量证据{index}] 来源：{hit['document']} · {hit['chapter']}"
            f"（分块 {hit['chunk_id']}，相似度 {hit['score']:.2f}）\n{hit['content']}"
        )

    for index, path in enumerate(graph_paths, start=1):
        blocks.append(
            f"[图谱证据{index}] 关系路径：{path['content']}"
            f"（路径 {path['path_id']}，置信度 {path['score']:.2f}）"
        )

    return "\n\n".join(blocks)


def build_sources(
    vector_hits: list[dict[str, Any]],
    graph_paths: list[dict[str, Any]],
) -> list[SourceItem]:
    """把两路证据转成前端可直接展示的“来源引用”列表。"""
    sources = [
        SourceItem(
            type="vector",
            title=hit["document"],
            detail=hit["chapter"],
            snippet=hit["content"],
        )
        for hit in vector_hits
    ]
    sources.extend(
        SourceItem(
            type="graph",
            title="知识图谱关系路径",
            detail=path["path_id"],
            snippet=path["content"],
        )
        for path in graph_paths
    )
    return sources


# ============================================================================
# 七、Prompt 组装（防幻觉的核心之一：证据约束）
# ============================================================================

SYSTEM_PROMPT = (
    "你是高校『校园服务与校纪校规』智能问答助手，服务对象是本校学生。\n"
    "必须严格遵守以下规则：\n"
    "1. 只能依据【背景资料】中的条款作答，禁止使用资料以外的知识，"
    "禁止编造条款编号、处分等级、办理流程；\n"
    "2. 若【背景资料】不足以回答问题，直接回答“抱歉，未找到相关校规或校园服务信息”，"
    "并建议学生咨询辅导员或学生工作处；\n"
    "3. 回答使用简体中文：先给结论，再逐条列出依据（注明文档名称与章节）；\n"
    "4. 语气客观严谨，不使用“应该”“大概”等模糊措辞。"
)

USER_PROMPT_TEMPLATE = (
    "【背景资料】\n{evidence}\n\n"
    "【用户问题】\n{query}\n\n"
    "请严格依据【背景资料】回答问题，并在答案末尾注明所引用的条款出处。"
)


def build_messages(query: str, evidence: str) -> list[dict[str, str]]:
    """组装 OpenAI 兼容格式的 messages 列表。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(evidence=evidence, query=query),
        },
    ]


# ============================================================================
# 八、DeepSeek 客户端（OpenAI SDK 调用，全局复用连接池）
# ============================================================================

_llm_client: AsyncOpenAI | None = None


def get_llm_client() -> AsyncOpenAI:
    """
    懒加载全局 AsyncOpenAI 客户端。
    之所以用 AsyncOpenAI：本项目的 /chat 是异步接口，流式读取时不会阻塞事件循环。
    """
    global _llm_client
    if _llm_client is None:
        _llm_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,  # DeepSeek 与 OpenAI 接口兼容，改 base_url 即可
            timeout=LLM_TIMEOUT_SECONDS,
        )
    return _llm_client


def ensure_api_key() -> None:
    """启动/请求前检查密钥，避免拿着空 key 去请求 API 才报一堆看不懂的错。"""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=500,
            detail=(
                "未配置 DEEPSEEK_API_KEY。请把 backend/.env.example 复制为 backend/.env，"
                "填入你的 DeepSeek API Key 后重启服务。"
            ),
        )


def friendly_llm_error(exc: Exception) -> str:
    """把 OpenAI SDK 的异常翻译成学生/演示时一眼能看懂的中文提示。"""
    if isinstance(exc, AuthenticationError):
        return "DeepSeek 鉴权失败：API Key 无效或已过期，请检查 backend/.env 中的 DEEPSEEK_API_KEY。"
    if isinstance(exc, RateLimitError):
        return "DeepSeek 调用频率超限或余额不足，请稍后重试或检查账户额度。"
    if isinstance(exc, APITimeoutError):
        return "DeepSeek 响应超时，请稍后重试（可适当调大 LLM_TIMEOUT_SECONDS）。"
    if isinstance(exc, APIConnectionError):
        return "无法连接 DeepSeek 服务，请检查网络、代理或 DEEPSEEK_BASE_URL 配置。"
    if isinstance(exc, APIStatusError):
        return f"DeepSeek 服务返回异常状态码 {exc.status_code}，请稍后重试。"
    return f"大模型调用失败：{type(exc).__name__}: {exc}"


# ============================================================================
# 九、MySQL 落库（Mock）
# ============================================================================

def save_to_mysql(query: str, answer: str) -> None:
    """
    问答记录落库（Mock）
    ------------------------------------------------------------------
    真实实现：INSERT INTO messages (conversation_id, role, content, sources, tokens)
              VALUES (...)，并更新 conversations.updated_at。
    当前实现：只 print，保证今晚不装 MySQL、不配 SQLAlchemy 也能跑通闭环。
    这个函数会被注册为 FastAPI 的 BackgroundTask，在响应返回客户端之后执行。
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    answer_preview = answer.replace("\n", " ")[:80]
    print(f"[MySQL-Mock] {timestamp} 已存入数据库：{answer_preview}（question={query[:40]}）")


# ============================================================================
# 十、SSE 流式输出工具
# ============================================================================

def sse_event(event: str, payload: dict[str, Any]) -> str:
    """按 SSE 规范编码一条事件：event 行 + data 行（JSON）+ 空行结尾。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def elapsed_ms(started_at: float) -> int:
    """从 perf_counter 起点计算已耗时（毫秒，取整）。"""
    return int((time.perf_counter() - started_at) * 1000)


async def answer_event_stream(
    query: str,
    evidence: str,
    sources: list[SourceItem],
    result_holder: dict[str, Any],
    started_at: float,
) -> AsyncIterator[str]:
    """
    正常回答的 SSE 事件流：
      meta（来源） → delta * N（逐字） → done（完整答案 + 耗时）
    其中 result_holder 用于把完整答案“带出”生成器，交给后台任务落库。
    """
    yield sse_event(
        "meta",
        {"evidence_found": True, "sources": [item.model_dump() for item in sources]},
    )

    pieces: list[str] = []
    try:
        async for piece in agent_generate(query, evidence):
            pieces.append(piece)
            yield sse_event("delta", {"content": piece})
    except Exception as exc:  # noqa: BLE001 —— 流式过程中任何异常都要转成前端可读的 error 事件
        print(f"[LLM-Error] {type(exc).__name__}: {exc}")
        yield sse_event("error", {"message": friendly_llm_error(exc)})
        return

    answer = "".join(pieces)
    result_holder.update(answer=answer, ok=True)
    yield sse_event(
        "done",
        {
            "answer": answer,
            "sources": [item.model_dump() for item in sources],
            "latency_ms": elapsed_ms(started_at),
            "refused": False,
        },
    )


async def reject_event_stream(
    result_holder: dict[str, Any],
    started_at: float,
) -> AsyncIterator[str]:
    """
    防幻觉拒答的 SSE 事件流：没有证据 → 不发 LLM 请求，直接把固定话术流式返回。
    保持与正常回答一致的事件结构，前端无需写两套解析逻辑。
    """
    result_holder.update(answer=NO_EVIDENCE_ANSWER, ok=True)
    yield sse_event("meta", {"evidence_found": False, "sources": []})
    yield sse_event("delta", {"content": NO_EVIDENCE_ANSWER})
    yield sse_event(
        "done",
        {
            "answer": NO_EVIDENCE_ANSWER,
            "sources": [],
            "latency_ms": elapsed_ms(started_at),
            "refused": True,
        },
    )


def save_stream_result(query: str, result_holder: dict[str, Any]) -> None:
    """流式回答结束后（响应已发出）再落库；失败的回答不写入，避免污染问答记录。"""
    if result_holder.get("ok"):
        save_to_mysql(query, result_holder.get("answer", ""))
    else:
        print(f"[MySQL-Mock] 本次回答生成失败，已跳过落库：{query[:40]}")


# ============================================================================
# 十一、核心 /chat 接口
# ============================================================================

@app.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        'event: meta\ndata: {"evidence_found": true, "sources": []}\n\n'
                        'event: delta\ndata: {"content": "根据"}\n\n'
                        'event: done\ndata: {"answer": "...", "latency_ms": 1200}\n\n'
                    ),
                }
            },
            "description": "stream=true 时返回 SSE 事件流；stream=false 时返回 ChatResponse JSON。",
        }
    },
    summary="多智能体问答（真实调用 DeepSeek，支持流式）",
    tags=["问答"],
)
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    """
    一条完整的问答主循环：
      ① 两个 Agent 并行召回（asyncio.gather）
      ② 证据融合
      ③ 防幻觉校验（无证据 → 拒答）
      ④ DeepSeek 流式生成（异常统一兜底）
      ⑤ 返回答案（流式 SSE 或普通 JSON）
      ⑥ 后台任务 print 模拟 MySQL 落库
    """
    started_at = time.perf_counter()

    # ① 检索 Agent 与图谱 Agent 并行执行：串行 0.30s → 并行约 0.15s
    vector_hits, graph_paths = await asyncio.gather(
        agent_retrieve(request.query),
        agent_graph(request.query),
    )

    # ② 多路证据融合
    evidence = fuse_evidence(vector_hits, graph_paths)
    sources = build_sources(vector_hits, graph_paths)

    # ③ 防幻觉校验：没有任何证据，直接拒答，绝不把空资料丢给大模型
    if not evidence.strip():
        if request.stream:
            result_holder: dict[str, Any] = {}
            background_tasks.add_task(save_stream_result, request.query, result_holder)
            return StreamingResponse(
                reject_event_stream(result_holder, started_at),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        background_tasks.add_task(save_to_mysql, request.query, NO_EVIDENCE_ANSWER)
        return ChatResponse(
            query=request.query,
            answer=NO_EVIDENCE_ANSWER,
            sources=[],
            evidence_found=False,
            latency_ms=elapsed_ms(started_at),
        )

    # ④ 有证据 → 真实调用 DeepSeek。先校验密钥，避免流式响应已经开始才报错。
    ensure_api_key()

    # ⑤-a 流式返回（技术亮点）：逐字 SSE，前端可做打字机效果
    if request.stream:
        result_holder = {}
        background_tasks.add_task(save_stream_result, request.query, result_holder)
        return StreamingResponse(
            answer_event_stream(request.query, evidence, sources, result_holder, started_at),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 告诉 Nginx 不要缓冲，保证真的流式
            },
        )

    # ⑤-b 非流式返回：用 Pydantic 模型返回结构化 JSON，方便 Swagger 调试与前端封装
    try:
        answer = await collect_answer(request.query, evidence)
    except Exception as exc:  # noqa: BLE001 —— 统一转成 502，避免把堆栈暴露给前端
        print(f"[LLM-Error] {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=friendly_llm_error(exc)) from exc

    # ⑥ 响应发出后由后台任务模拟落库（Mock）
    background_tasks.add_task(save_to_mysql, request.query, answer)
    return ChatResponse(
        query=request.query,
        answer=answer,
        sources=sources,
        evidence_found=True,
        latency_ms=elapsed_ms(started_at),
    )


async def collect_answer(query: str, evidence: str) -> str:
    """把流式 token 拼成完整答案（非流式接口复用同一套生成逻辑）。"""
    pieces: list[str] = []
    async for piece in agent_generate(query, evidence):
        pieces.append(piece)
    return "".join(pieces)


# ============================================================================
# 十二、辅助接口：健康检查 + 极简演示页
# ============================================================================

@app.get("/health", summary="健康检查", tags=["运维"])
async def health() -> dict[str, Any]:
    """用于快速确认服务是否启动、密钥是否已配置（不会返回密钥内容）。"""
    return {
        "status": "ok",
        "llm_configured": bool(DEEPSEEK_API_KEY),
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/", include_in_schema=False)
async def demo_page() -> FileResponse:
    """返回一个极简网页，用浏览器就能直观看到 SSE 流式输出效果。"""
    page = BASE_DIR / "demo.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="demo.html 不存在，请访问 /docs 调试接口。")
    return FileResponse(page, media_type="text/html")
