# v2 架构设计（课程答辩版）

> 状态：**Day1 已完成**（图引擎 + 10+ 节点 + SSE 轨迹 + Mock 适配器）
> **Day2 已完成**：真实 Qdrant（本地嵌入式）+ 本地 BGE、MySQL 9 表建库、问答落库、会话历史、点赞点踩；真实 DeepSeek 端到端跑通。
> **Day3（提前）已完成**：校规文档解析入库流水线 + Neo4j 校规图谱，真实三库并行召回已联调；20 项测试全过。
> 目标：1 周内交付可现场演示的课程答辩系统，之后平滑扩展为正常平台。
> 注意：MySQL 暂不升级（5.5.11 → 8.0），脚本已兼容两者；升级安排在答辩后。

## 0. 本机环境复用（不需要 Docker、不需要下载模型）

调试时发现本机已具备完整的真实链路环境，直接复用（全部原生/嵌入式，与 Docker 无关）：

| 组件 | 位置 / 版本 | 用法 | 实测 |
| --- | --- | --- | --- |
| Qdrant | `qdrant-client` 本地嵌入式模式 | `QdrantClient(path=backend/data/qdrant)`，数据落盘 | ✅ 建集合、写入、语义检索通过 |
| BGE-small-zh | `G:\ai-models\hf\hub\models--BAAI--bge-small-zh-v1.5\snapshots\7999e1d...` | `EMBEDDING_MODEL_PATH` 指向该目录，零下载 | ✅ 512 维，冷启动加载约 2~3 分钟，之后单次检索约 0.05s |
| Neo4j | `G:\neo4j` · 5.26.30 · `auth_enabled=false` | `scripts/start_neo4j.ps1` 使用本机 Java17 启动 | ✅ 已灌入样例手册：1 文档 / 8 章节 / 26 条款 / 7 主题 / 74 关系 |
| MySQL | 本机服务 5.5.11，root 无密码 | `MYSQL_URL` + `school_rules_rag` 库（`sql/init_mysql.sql`） | ✅ 9 张表 + 种子数据（admin/student、6 标签） |

> 注意：`backend/data/` 已加入 `.gitignore`；没有改动你原来 `G:\智能体学习\qdrant_data` 里的课程数据。

## 1. 答辩要求 → 实现映射

| 课程要求 | 实现 |
| --- | --- |
| 智能体设计、完整图构建、≥5 节点 | 13 个节点（11 个 Agent 节点 + 1 个并行分组 + 1 个终态节点），见 `app/agents/graph.py` |
| 至少 2 种控制结构 | 三种全有：并行（双路召回）、选择（缓存 / 充分性 / 引用校验）、循环（查询扩展重召回、引用校验重生成） |
| 完整前后端 | 后端 `backend/app`（FastAPI + SSE）；前端 `frontend/`（Vue3，Day3 开始） |
| 实际系统演示 | `/api/v1/chat` SSE 实时输出答案 + Agent 轨迹 |
| 不能用课程演示系统 | 自研系统，与毕设题目一致 |

## 2. 分层架构

```text
Vue3 SPA ── /api/v1/* + SSE ──> FastAPI (app/main.py)
                                     │
                          WorkflowEngine（并行 / 选择 / 循环 + trace）
                                     │
         11 个 Agent 节点（查询理解 / 检索 / 图谱 / 融合 / 充分性 / 扩展 /
                            生成 / 引用校验 / 拒答 / 降级 / 落库）
                                     │
        Services 适配器（Mock ⇄ Qdrant / Neo4j / MySQL / DeepSeek）
```

**关键原则**：节点只依赖适配器接口；切换真实存储只改 `.env` 的 `*_MODE`，节点代码不动。

## 3. Agent 图

```mermaid
flowchart TD
    A([用户提问]) --> B{语义缓存命中?}
    B -->|命中| P[落库 / 缓存]
    B -->|未命中| C[① 查询理解 Agent]
    C --> D1[② 检索 Agent]
    C --> D2[③ 图谱 Agent]
    D1 --> E[④ 证据融合 Agent]
    D2 --> E
    E --> F{⑤ 充分性判定}
    F -->|不足 且 轮次<2| G[⑥ 查询扩展 Agent]
    G --> D1
    G --> D2
    F -->|不足 且 轮次用尽| H[⑨ 拒答 + 人工引导]
    F -->|充分| I[⑦ 生成 Agent]
    I --> J{⑧ 引用校验}
    J -->|不通过 且 次数未超| I
    J -->|多次不通过| K[⑩ 降级回答]
    J -->|通过| P
    H --> P
    K --> P
```

## 4. 控制结构落点（答辩讲解用）

| 控制结构 | 图位置 | 演示看点 |
| --- | --- | --- |
| 并行 | 查询理解后 fan-out 到检索 / 图谱，`asyncio.gather` 汇合 | 轨迹面板两条分支同时 start，分支耗时分别展示 |
| 选择 | 缓存命中短路；充分性判定三分支；引用校验两分支 | 问“宿舍几点熄灯？”看缓存短路；问“今天天气怎么样？”看拒答分支 |
| 循环 | `check_sufficiency → expand_query → 并行召回`（≤2 轮）；`verify_grounding → generate_answer`（≤1 次） | 问“晚上不回寝室会怎样？”看扩展后第二轮召回；轨迹里两条 `route` 回到前序节点 |

循环有硬上限：`MAX_RETRIEVAL_ROUNDS`、`MAX_REGEN`、`MAX_STEPS`（全局步数）、单节点 `NODE_TIMEOUT`。

## 5. SSE 协议

| 事件 | 含义 |
| --- | --- |
| `meta` | 证据来源（融合后推送；缓存命中时带 `cached: true`） |
| `trace` | 节点轨迹：`{node, phase: start/end/route, kind, control, latency_ms, from, to, reason}` |
| `delta` | 大模型逐字增量 |
| `done` | 完整答案、来源、`refused/degraded/cached`、`runtime`（步数 / 访问节点 / 循环次数） |
| `error` | 终止错误 |

### REST 接口（Day2 已实现）

| 接口 | 说明 |
| --- | --- |
| `POST /api/v1/chat` | 多智能体问答（`stream=true` SSE；`stream=false` JSON） |
| `GET /api/v1/graph` | Agent 图定义（节点 / 边 / 控制结构），供前端画图 |
| `GET /api/v1/conversations` | 会话列表（含消息数） |
| `GET /api/v1/conversations/{id}` | 会话详情（完整消息 + 来源引用） |
| `POST /api/v1/messages/{id}/feedback` | 点赞 / 点踩 |
| `POST /api/v1/documents/upload` | 上传文档并入库（JSON：md/txt 文本 或 PDF 的 base64） |
| `POST /api/v1/documents/upload-file` | multipart 文件上传（安装 python-multipart 后自动出现） |
| `GET /api/v1/documents` | 文档列表（含分块数/状态） |
| `GET /api/v1/documents/{id}` | 文档详情（分块预览 + qdrant_point_id） |
| `DELETE /api/v1/documents/{id}` | 删除文档（MySQL + Qdrant + Neo4j 同步清理） |
| `GET /health` | 健康检查（模式 / 图节点数 / 向量预热状态） |

## 5.1 校规文档入库流水线（已完成）

```text
rules/*.md|*.txt
   → rules_parser（识别「第X章」「第N条」，切分为条款）
   → MySQL：documents + chunks（记录 qdrant_point_id 溯源映射）
   → BGE-small-zh 向量化
   → Qdrant：写入 26 个向量点（payload 带文档/章节/条款）
   → Neo4j：(RuleDocument)-[:HAS_CHAPTER]->(Chapter)-[:HAS_RULE]->(Rule)-[:ABOUT]->(Topic)
```

命令：`cd backend && python scripts/ingest_rules.py --dir ../rules --reset`
（注意：Qdrant 本地模式同一目录只能被一个进程打开，跑命令行入库前请先停后端。）

后端运行中上传：`POST /api/v1/documents/upload`（JSON：filename / content 或 content_base64 PDF / title / replace），
复用已加载的 BGE 与 Qdrant 客户端；删除文档时同步清理 Qdrant 向量与 Neo4j 图谱节点。

PDF 策略：文字版 PDF 用 PyMuPDF 直接提取（零额外依赖，已实测）；扫描件自动回退 OCR
（`rapidocr_onnxruntime`，未安装时返回明确的安装提示）。切块优先按「第X章/第N条」，
无结构时按 600 字+80 字重叠兜底，超长块再拆分。

图谱检索方式：问题关键词在 `Rule.content/article/Chapter.title` 上做包含匹配，命中后沿"文档 → 章节 → 条款 → 主题"遍历，返回带路径信息的证据（中文小语料下比全文索引更稳）。

## 6. 目录

```text
backend/
├── app/
│   ├── main.py              # 应用工厂 + CORS + /health + 启动预热
│   ├── core/                # config.py（模式开关）、security.py（PBKDF2 密码哈希）
│   ├── schemas/             # chat.py / conversation.py（Pydantic）
│   ├── agents/
│   │   ├── state.py         # AgentState
│   │   ├── events.py        # EventBus（SSE 事件总线）
│   │   ├── runtime.py       # 图引擎（并行 / 选择 / 循环 + trace）
│   │   ├── graph.py         # 图定义（节点 + 路由）
│   │   └── nodes.py         # 节点实现 + Prompt
│   ├── services/            # llm / stores（Qdrant+BGE、Neo4j）/ persistence / rules_parser / ingestion
│   ├── db/                  # models.py（SQLAlchemy）、session.py
│   ├── api/v1/              # chat.py、conversations.py
│   ├── scripts/             # ingest_rules.py（文档入库命令行）
│   └── data/                # 本地 Qdrant 数据（gitignore）
├── main.py                  # 旧极简版（现场回退）
├── tests/                   # 图引擎 / SSE / MySQL / 解析器 / Neo4j
└── rules/                   # 校规文档目录（当前为虚构样例）
```

## 7. 排期

| 天 | 内容 | 状态 |
| --- | --- | --- |
| D1 | 图引擎 + 10+ 节点 + SSE trace + Mock 适配器 + 测试 | ✅ 完成 |
| D2 | Qdrant 本地模式 + 本地 BGE 小语料入库；MySQL 9 表 + 落库 + 会话历史 + 反馈 | ✅ 完成 |
| D3 | Vue3：登录 + 问答（SSE/轨迹/来源/反馈） | ⏳ |
| D4 | 文档上传/列表/详情/删除 API（✅ 已完成）；会话历史前端 + 文档管理前端 + 联调 | 🔄 |
| D5 | Neo4j 校规图谱灌入 + `GRAPH_MODE=neo4j` 联调（服务已在运行） | ✅ 完成（含文档入库流水线，真实三库联调通过） |
| D6 | 全链路彩排 + 种子数据 + 一键兜底 | ⏳ |
| D7 | PPT + 实验报告 + 演示视频脚本 | ⏳ |
