# 基于 RAG 与多智能体协作的校纪校规问答平台

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue3-4FC08D?logo=vue.js&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-5.x-4581C3?logo=neo4j&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-1.x-FF9900?logo=qdrant&logoColor=white)
![License](https://img.shields.io/badge/License-Apache%202.0-blue)

> 毕业设计（2026.09 - 至今）· 独立开发
> 面向高校学生的校纪校规垂直领域问答平台：以 **GraphRAG + 多智能体协作** 为核心，融合向量语义召回与知识图谱多跳推理，并通过证据约束机制抑制大模型幻觉。

🔗 项目地址：<https://github.com/xc-0525/school-rules-rag-platform>

---

## 📌 当前进度（2026-10-26 更新）

> **两套可运行版本**：
> 1. **极简版** [`backend/main.py`](backend/main.py)：单文件 MVP，Mock 双 Agent + DeepSeek 流式，40 项冒烟检查通过；
> 2. **v2 正式工程** [`backend/app/`](backend/app/)：自研 Agent 图引擎（13 节点 / 并行·选择·循环 / SSE 轨迹）+ 真实 MySQL 落库 + 会话历史 + 真实 Qdrant（本地嵌入式 + 本地 BGE）+ 真实 Neo4j 图谱 + 真实 DeepSeek，**全链路已端到端跑通**。

| 模块 | 当前状态 | 代码位置 |
| --- | --- | --- |
| 极简版主循环 `POST /chat` | ✅ 可直接运行 | [`backend/main.py`](backend/main.py) |
| v2 Agent 图引擎（13 节点，并行/选择/循环） | ✅ 已完成，SSE 实时输出轨迹 | [`backend/app/agents/`](backend/app/agents/) |
| v2 真实向量检索（Qdrant 本地嵌入式 + BGE-small-zh） | ✅ 已实测（512 维，检索约 0.05s/次） | [`backend/app/services/stores.py`](backend/app/services/stores.py) |
| v2 MySQL 落库 / 会话历史 / 点赞点踩 | ✅ 9 张表 + 真实 DeepSeek 全链路通过 | [`backend/app/services/persistence.py`](backend/app/services/persistence.py) |
| v2 真实 Neo4j 图谱 | ✅ 已灌入样例手册（1 文档 / 8 章节 / 26 条款 / 7 主题），双路召回已验证 | [`backend/app/services/stores.py`](backend/app/services/stores.py) |
| 校规文档入库流水线 | ✅ 解析 → MySQL → BGE → Qdrant → Neo4j（命令行 + 上传接口） | [`backend/scripts/ingest_rules.py`](backend/scripts/ingest_rules.py) |
| 文档上传 / 列表 / 详情 / 删除 API | ✅ `.md/.txt` + 文字版 PDF（base64）；扫描件自动 OCR（已实测，单页约 8s） | [`backend/app/api/v1/documents.py`](backend/app/api/v1/documents.py) |
| 校规样例文档 | ✅ 已提供（虚构样例，替换真实文档后重跑入库即可） | [`rules/学生手册节选.md`](rules/学生手册节选.md) |
| Vue3 前端（问答 / 轨迹 / 历史 / 文档） | ✅ 已完成：SSE 流式问答 + Agent 轨迹 + 来源引用 + 会话历史 + 文档管理 | [`frontend/`](frontend/) |
| 登录鉴权（admin / student） | ✅ 登录页 + 学生自助注册 + Bearer token + 按用户隔离会话；管理员才能上传/删除文档 | [`backend/app/services/auth.py`](backend/app/services/auth.py) |
| 数据库建表脚本 | ✅ 已补齐，兼容 MySQL 5.5 / 8.0 | [`sql/init_mysql.sql`](sql/init_mysql.sql) |

**答辩演示启动顺序**：

```powershell
# 1) 启动 Neo4j（单独开一个窗口，保持运行）
powershell -ExecutionPolicy Bypass -File .\scripts\start_neo4j.ps1

# 2) 首次/更新校规文档时入库（BGE 首次加载约 2~3 分钟）
cd backend
python scripts/ingest_rules.py --dir ../rules --reset

# 3) 启动后端（使用全局 Python；同样需要等 BGE 预热）
cd ..
powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1

# 4) 启动前端（另开窗口；Vite 代理到 8000）
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1
# 浏览器：http://127.0.0.1:5173
# 演示账号：admin / admin123（管理员）、student / student123（学生）
# 登录页也支持自助注册（默认学生角色），可用新账号演示"自己的历史会话"
```

> 手动启动：`cd backend; uvicorn app.main:app --port 8000`（默认 mock 模式，秒起，适合离线兜底）。
> 实际演示用 `start_backend.ps1` 时会自动设置 `AUTH_REQUIRED=1`（所有业务接口需带 Bearer token）。
> 当前测试：**38 项自动化测试全过**（图引擎 + SSE + MySQL 落库/历史/反馈 + 文档解析 + PDF + Neo4j 图谱 + 上传接口校验 + 缓存/引用校验回归 + OCR 文本归一化 + multipart 路由 + 登录/注册/鉴权/会话隔离）；
> 真实三库 + 真实 DeepSeek 已验证：答案同时带「知识图谱 · 文档/章节/条款」与「向量检索」两类来源。
> 本轮已修复：降级答案不再进缓存、文档上传/删除后清空缓存、上传前预检 Neo4j 并在失败时回滚半成品、
> 图谱标题引用校验误判、启动脚本固定全局 Python。

### ⚠️ 已知限制与待补事项

1. **校规内容为虚构样例**：`rules/学生手册节选.md` 只用于跑通链路，拿到学校真实文件后替换并重新入库即可；
2. **扫描件 OCR 已可用但建议人工抽查**：`rapidocr_onnxruntime` + Pillow 已安装，实测单页约 8 秒；
   OCR 可能把「第1条」识别成「第1 条」（已做文本归一化修正），但真实扫描件的错字/页眉页脚仍需入库前抽查；
3. **登录是演示级 token**：token 存在后端进程内存里（8 小时），重启后端需重新登录；未做 JWT/刷新令牌，
   学生/管理员权限只区分"文档管理"；注册未做邮箱验证/验证码/审核，适合课程演示，不适合直接上线；
4. **Swagger UI 中的流式响应是“一次性显示”的**：想看打字机效果请用前端 <http://127.0.0.1:5173> 或 `curl -N`；
5. **“语义缓存”实际是问题归一化 + 精确匹配**：答辩时建议称“问题归一化缓存”，真正的向量语义缓存是后续计划；
6. 本地 MySQL 是 **5.5.11**（较老），脚本已做兼容；答辩后再评估升级 8.0。

---

## 一、项目背景

学生在查询校纪校规时长期面临三类痛点：

1. **信息分散** —— 规定散落在学籍管理、违纪处分、奖助学金、宿舍管理等多份 PDF/Word 文档中，缺乏统一入口；
2. **检索低效** —— 关键词检索难以匹配语义相近的表述，人工翻阅成本高；
3. **大模型幻觉** —— 通用大模型不掌握校内具体规定，容易编造条款，而校规场景对答案的**严谨性与可溯源性**要求极高。

本项目独立设计并实现了一套基于 **RAG（检索增强生成）与多智能体协作** 的垂直问答平台，将"向量语义检索 + 图谱关系推理 + 证据约束生成"结合，输出**带条款来源引用**的可靠回答。

## 二、核心特性

- **多库混合存储架构**：MySQL（业务数据 + 分块元数据）、Neo4j（校规知识图谱）、Qdrant（向量索引）三库分工，通过 `qdrant_point_id` 映射实现**数据溯源**。
- **多智能体协作编排**：设计"**检索 Agent → 图谱 Agent → 生成 Agent**"工作流，Qdrant 向量召回与 Neo4j 多跳推理并行执行，多路证据统一交由生成 Agent 产出答案。
- **本地中文 Embedding**：使用本地部署的 `BGE-small-zh` 生成向量，数据不出校、调用零成本，适合中文校规文本。
- **防幻觉机制**：
  - 证据约束提示词，答案只能基于召回的条款片段生成；
  - 每条回答附带来源引用（文档、章节、分块 ID），支持逐条核对；
  - 对无证据支撑的问题触发"**拒答并引导人工咨询**"机制，杜绝编造。
- **高频问题归一化缓存**：对高频问题去掉标点/空格/大小写差异后精确匹配，命中即返回，降低 DeepSeek 调用延迟与成本（真正的向量语义缓存为后续计划）。
- **完整业务闭环**：多轮会话、问答记录、点赞/点踩反馈、文档上传与处理状态跟踪、标签分类、管理员公告、学生/管理员角色权限。

## 三、系统架构

```mermaid
flowchart TB
    subgraph Client["前端层"]
        U[学生 / 管理员]
        FE["Vue3 + Vite<br/>会话问答 · 文档管理 · 公告反馈"]
    end

    subgraph Server["FastAPI 后端"]
        API["REST API / SSE 流式问答"]
        CACHE[(高频问题<br/>语义缓存)]
        subgraph Agents["多智能体编排层"]
            RA["检索 Agent<br/>向量语义召回"]
            GA["图谱 Agent<br/>多跳关系推理"]
            GE["生成 Agent<br/>证据约束生成 / 拒答判定"]
        end
    end

    subgraph Storage["混合存储层"]
        MYSQL[("MySQL<br/>业务数据 · 分块元数据 · ID 映射")]
        QDRANT[("Qdrant<br/>BGE 向量索引")]
        NEO4J[("Neo4j<br/>校规知识图谱")]
    end

    EMB["本地 BGE-small-zh<br/>Embedding 模型"]
    LLM["DeepSeek<br/>大语言模型"]

    U --> FE -->|"HTTP / SSE"| API
    API --> CACHE
    API --> RA & GA
    RA -->|"文本向量化"| EMB
    RA --> QDRANT
    GA --> NEO4J
    QDRANT -. "qdrant_point_id 映射" .- MYSQL
    RA -->|"证据片段"| GE
    GA -->|"关系路径"| GE
    GE -->|"证据约束 Prompt"| LLM
    GE -->|"问答记录 / sources"| MYSQL
```

### 问答工作流

```mermaid
flowchart LR
    Q[用户提问] --> C{语义缓存<br/>是否命中?}
    C -->|命中| A0[直接返回缓存答案]
    C -->|未命中| E[BGE-small-zh 向量化]
    E --> R[检索 Agent<br/>Qdrant 语义召回 Top-K 条款]
    Q --> G[图谱 Agent<br/>Neo4j 多跳推理]
    R --> M[多路证据融合<br/>映射回 MySQL 溯源]
    G --> M
    M --> J{证据是否充分?}
    J -->|否| X["拒答 + 引导人工咨询"]
    J -->|是| W[生成 Agent<br/>DeepSeek 证据约束生成]
    W --> A1[答案 + 条款来源引用]
    A1 --> S[写入语义缓存]
```

## 四、技术栈

| 层次 | 技术选型 |
| --- | --- |
| 前端 | Vue 3、Vite、TypeScript |
| 后端 | Python、FastAPI、SQLAlchemy、SSE 流式响应 |
| 关系数据库 | MySQL 8.0（InnoDB / utf8mb4） |
| 图数据库 | Neo4j 5.x（校规实体与关系、多跳推理） |
| 向量数据库 | Qdrant（BGE 向量存储与 ANN 检索） |
| Embedding | 本地 BGE-small-zh（中文向量模型） |
| 大模型 | DeepSeek（证据约束答案生成） |
| 智能体 | Multi-Agent 编排：检索 Agent / 图谱 Agent / 生成 Agent |

## 五、数据库设计

### 5.1 三库存储分工

| 存储 | 职责 | 说明 |
| --- | --- | --- |
| **MySQL** | 业务数据、文档/分块元数据、ID 映射 | 向量与图谱均**不冗余**存储于此，仅通过 ID 关联 |
| **Qdrant** | 分块向量及 ANN 索引 | 向量点 ID 回写 `chunks.qdrant_point_id` |
| **Neo4j** | 校规知识图谱（节点 + 关系） | 支撑实体关联查询与多跳推理 |

**数据溯源链路**：

```text
Qdrant 向量点 → chunks.qdrant_point_id → chunks.document_id
            → documents（原始文件 / 章节）→ messages.sources（JSON）→ 前端来源引用
```

### 5.2 ER 关系

```mermaid
erDiagram
    users ||--o{ conversations : "发起"
    users ||--o{ documents : "上传"
    users ||--o{ feedback : "提交"
    users ||--o{ announcements : "发布"
    conversations ||--o{ messages : "包含"
    messages ||--o{ feedback : "接收"
    documents ||--o{ chunks : "切分为"
    documents ||--o{ document_tag_relations : ""
    document_tags ||--o{ document_tag_relations : ""
```

### 5.3 核心表一览（共 9 张）

| # | 表名 | 说明 | 关键字段 |
| --- | --- | --- | --- |
| 1 | `users` | 用户表 | `username` 唯一、`password`（bcrypt）、`role`（student/admin）、`status` |
| 2 | `conversations` | 会话表（一次连续多轮问答） | `user_id`、`title`、`updated_at` |
| 3 | `messages` | 问答记录表 | `role`、`content`、`sources`（来源 JSON）、`tokens` |
| 4 | `documents` | 校规文档表（PDF/Word/TXT） | `file_path`、`chunk_count`、`status`（pending/processing/done/failed） |
| 5 | `chunks` | 文档分块表（**Qdrant 映射**） | `qdrant_point_id` 唯一、`chapter`、`chunk_index` |
| 6 | `feedback` | 用户反馈表 | `message_id`、`rating`（1 赞 / -1 踩）、`comment` |
| 7 | `announcements` | 公告表 | `title`、`is_pinned`、`status`（发布/草稿） |
| 8 | `document_tags` | 文档标签表 | `name` 唯一（学籍管理、违纪处分等） |
| 9 | `document_tag_relations` | 文档-标签关联表（多对多） | `document_id` + `tag_id` 联合唯一 |

> 建表脚本：`sql/init_mysql.sql`（兼容 MySQL 5.5+ / 8.0，utf8mb4 / InnoDB，含外键级联、索引与种子数据）。
> 已在本地 MySQL 5.5.11 上实测通过（9 张表 + 2 个账号 + 6 个标签）。

### 5.4 初始化数据

- 默认账号：`admin` / `admin123`（管理员）、`student` / `student123`（学生）；密码为 PBKDF2-SHA256 哈希（见 `app/core/security.py`），**上线前请务必修改密码**
- 默认文档标签：学籍管理、违纪处分、奖助学金、宿舍管理、教学管理、校园安全

## 六、快速开始

> 项目现在有**两套可运行版本**：6.2 的极简版（零数据库依赖）和 6.3 起的 v2 正式工程（真实 MySQL + Qdrant/BGE，Neo4j 待接入）。
> 答辩演示建议用 v2；现场若外部依赖异常，可随时切回极简版或把 v2 切到 Mock 模式。

### 6.1 环境要求

| 场景 | 需要准备 |
| --- | --- |
| 极简版 Demo（当前可跑） | Python 3.10+、DeepSeek API Key |
| 完整版（规划中） | 另需 Node.js 18+、MySQL 8.0+、Neo4j 5.x、Qdrant 1.x、本地 BGE-small-zh 模型 |

### 6.2 极简版 Demo（当前可运行 · 无需任何数据库）

#### ① 安装依赖

```bash
cd backend
python -m venv .venv
# Windows PowerShell:      .\.venv\Scripts\Activate.ps1
# Windows CMD:             .venv\Scripts\activate.bat
# Linux / macOS:           source .venv/bin/activate
pip install -r requirements.txt
```

> 也可以跳过虚拟环境，直接 `pip install -r requirements.txt` 后用系统 Python 启动。

#### ② 配置 `.env`

```bash
copy .env.example .env               # 若 backend/.env 已存在可跳过；Linux/macOS 用 cp .env.example .env
```

编辑 `backend/.env`，把 `DEEPSEEK_API_KEY` 换成自己的真实 Key（其余保持默认即可）：

```env
DEEPSEEK_API_KEY=sk-你的真实密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
LLM_TEMPERATURE=0.2
LLM_TIMEOUT_SECONDS=60
```

> `.env` 已被 `.gitignore` 忽略，不会被提交到仓库。

#### ③ 启动服务

```bash
uvicorn main:app --reload --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 即启动成功。
每次问答结束后，终端会打印一行 `[MySQL-Mock] ... 已存入数据库：...`，代表 Mock 落库成功。

#### ④ Swagger UI 测试

1. 浏览器打开 <http://127.0.0.1:8000/docs>；
2. 展开 `POST /chat` → 点击 **Try it out**；
3. 请求体填（先用 `stream=false` 看结构化结果）：

```json
{
  "query": "宿舍晚上几点熄灯？",
  "stream": false
}
```

4. 点击 **Execute**，可看到 `answer` / `sources` / `evidence_found` / `latency_ms` 四个字段；
5. 把 `stream` 改成 `true` 再执行，响应体会变成 SSE 原始事件流（`event: meta` / `event: delta` / `event: done`）。
   注意 Swagger 会把流式响应**一次性**显示出来，想看“打字机”效果请用第 ⑤ 步的演示页。

#### ⑤ 浏览器流式演示（答辩演示推荐）

打开 <http://127.0.0.1:8000/>，页面内置 5 个示例问题，答案逐字输出，并在下方展示向量 / 图谱来源引用。

#### ⑥ curl 直接测试

```bash
# 非流式
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"宿舍晚上几点熄灯？","stream":false}'

# 流式：-N 关闭 curl 自身缓冲，才能看到逐块输出
curl -N -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"考试时带手机进考场会怎么处理？","stream":true}'
```

#### ⑦ SSE 事件格式（前端对接用）

| 事件 | data 示例 | 说明 |
| --- | --- | --- |
| `meta` | `{"evidence_found":true,"sources":[...]}` | 证据召回结果，先于正文推送 |
| `delta` | `{"content":"根据"}` | 大模型逐字增量，前端追加渲染即可 |
| `error` | `{"message":"DeepSeek 鉴权失败……"}` | 终止事件，之后不会再有 `done` |
| `done` | `{"answer":"...","latency_ms":1200,"refused":false}` | 完整答案、耗时、是否为防幻觉拒答 |

#### ⑧ 演示问题清单

| 提问 | 预期表现 |
| --- | --- |
| 宿舍晚上几点熄灯？ | 命中向量 + 图谱证据，正常流式作答 |
| 考试时带手机进考场会怎么处理？ | 命中考试纪律条款，正常作答 |
| 国家奖学金需要什么条件？ | 命中奖学金条款，正常作答 |
| 请假三天以上怎么审批？ | 命中考勤条款，正常作答 |
| 今天天气怎么样？ | `evidence_found=false`，直接拒答且**不调用大模型** |

### 6.3 初始化 MySQL（v2 正式工程）

> 脚本已就绪并通过本机 MySQL 5.5.11 实测；MySQL 8.0 同样兼容。

```bash
# 创建数据库 school_rules_rag、建 9 张表并写入种子账号/标签
mysql -u root -p --default-character-set=utf8mb4 < sql/init_mysql.sql
```

然后在 `backend/.env` 中配置：

```env
MYSQL_URL=mysql+pymysql://root:你的密码@127.0.0.1:3306/school_rules_rag?charset=utf8mb4
```

### 6.4 启动 Qdrant / Neo4j（本机原生 · 不需要 Docker）

本项目不使用 Docker：

- **Qdrant**：用 `qdrant-client` 的本地嵌入式模式，数据落在 `backend/data/qdrant`，无需启动服务；
- **BGE-small-zh**：直接读取本机缓存模型（`.env` 的 `EMBEDDING_MODEL_PATH`），无需下载；
- **Neo4j**：本机原生 5.26.30 + Java17，用脚本启动：

```powershell
# 前台运行，演示期间保持窗口打开；另开一个窗口跑后端
powershell -ExecutionPolicy Bypass -File .\scripts\start_neo4j.ps1
# 或
.\scripts\start_neo4j.cmd
```

启动后 bolt：`127.0.0.1:7687`（本机配置已关闭认证），浏览器控制台：<http://127.0.0.1:7474>。

### 6.5 v2 后端配置（完整 `.env` 参考）

```env
# 运行模式：mock | qdrant / mock | neo4j / deepseek | fake
VECTOR_MODE=qdrant
GRAPH_MODE=neo4j
LLM_MODE=deepseek

# MySQL
MYSQL_URL=mysql+pymysql://root:你的密码@127.0.0.1:3306/school_rules_rag?charset=utf8mb4

# Qdrant 本地嵌入式 + 本机 BGE 模型
QDRANT_PATH=./data/qdrant
QDRANT_COLLECTION=rules_chunks
EMBEDDING_MODEL_PATH=G:\ai-models\hf\hub\models--BAAI--bge-small-zh-v1.5\snapshots\7999e1d3359715c523056ef9478215996d62a620

# Neo4j（本机已关闭认证）
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=
NEO4J_PASSWORD=

# DeepSeek
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 登录鉴权：1=业务接口必须带 Bearer token（start_backend.ps1 默认开启），0=允许匿名
AUTH_REQUIRED=1

# 是否开放学生自助注册：1=开放（默认），0=关闭
ALLOW_REGISTRATION=1
```

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 6.6 校规文档入库（真实数据）

**方式一：上传接口（推荐，后端运行时使用）**

`POST /api/v1/documents/upload`（JSON 上传，默认**不需要任何新依赖**）：

| 文件类型 | 请求字段 | 依赖 |
| --- | --- | --- |
| `.md` / `.txt` | `content`（UTF-8 文本） | 无 |
| `.pdf`（文字版） | `content_base64`（文件 base64） | PyMuPDF（本机已装 ✅，零额外安装） |
| `.pdf`（扫描件） | 同上，自动尝试 OCR | 需额外安装 OCR 引擎（见下表） |

```bash
# .md / .txt 文本
curl -X POST http://127.0.0.1:8000/api/v1/documents/upload \
  -H "Content-Type: application/json" \
  -d '{"filename":"校园服务指南.md","content":"# XX大学……\n\n## 第一章……\n\n第1条……","replace":true}'

# 文字版 PDF（base64）
curl -X POST http://127.0.0.1:8000/api/v1/documents/upload \
  -H "Content-Type: application/json" \
  -d '{"filename":"学生手册.pdf","content_base64":"JVBERi0xLjQK...","replace":true}'
```

响应示例：`{"document_id":2,"chunk_count":16,"chapters":6,"status":"done","message":"入库成功"}`。
配套接口：`GET /api/v1/documents`（列表）、`GET /api/v1/documents/{id}`（详情+分块）、
`DELETE /api/v1/documents/{id}`（同时清理 Qdrant 向量与 Neo4j 图谱）。
Swagger <http://127.0.0.1:8000/docs> 里也能直接测。

> `python-multipart` 已安装，所以 `POST /api/v1/documents/upload-file`（multipart：file / title / replace）
> 也在 Swagger 里可用；前端仍走 JSON 上传，不强依赖 multipart。

**PDF / OCR 依赖说明（也就是"为什么要不要装 python-multipart"）**

| 需求 | 是否必须 | 说明 |
| --- | --- | --- |
| `python-multipart` | ❌ 不是必须 | **只和"怎么传文件"有关**，跟 PDF 解析、OCR、切块都无关；用 JSON/base64 上传就完全不需要 |
| PyMuPDF | ✅ 本机已装（1.28.2） | PDF 文字层提取；扫描件时用它把页面渲染成图片交给 OCR |
| `rapidocr_onnxruntime` + `pillow` | 仅扫描件需要 | 推荐 OCR 方案：中文友好、无需外部 exe |
| `pytesseract` + Tesseract | 备选 OCR | 需另装 Tesseract 程序 + `chi_sim` 中文语言包 |
| `pypdf` | 可选 | 本机已装，PyMuPDF 提取失败时可作为备用（当前未启用） |

安装命令（要在**你自己的终端**执行；演示脚本用的是全局 Python）：

```powershell
# 可选：启用标准 multipart 文件上传接口
& G:\DevEnv\Python\Python314\python.exe -m pip install python-multipart

# 扫描件 PDF OCR（推荐方案）
& G:\DevEnv\Python\Python314\python.exe -m pip install rapidocr_onnxruntime pillow
```

**切块逻辑**（PDF 文字层 / OCR 结果共用同一解析器）：

1. 优先按「第X章 / 第N条」结构化切块；
2. 没有条款结构时，按 600 字 + 80 字重叠兜底切块；
3. 单块超过 1200 字自动再拆分，避免向量过长影响召回；
4. OCR 文本建议入库前人工抽查（扫描件可能有错字、页眉页脚混入）。

**方式二：命令行批量入库（后端停止时使用）**

```bash
cd backend
python scripts/ingest_rules.py --dir ../rules --reset
```

> ⚠️ Qdrant 本地嵌入式模式同一目录只能被一个进程打开：**跑命令行入库前先停掉后端**；
> 后端运行时请用上传接口。命令行目前只处理 `.md` / `.txt`，PDF 请走上上传接口。
> 仓库内 `rules/学生手册节选.md`（26 条）和 `rules/校园服务指南节选.md`（16 条）都是虚构样例；
> 当前库内为 2 份文档 / 42 个条款（Qdrant 42 个向量、Neo4j 42 个条款节点）。

### 6.7 前端启动（Vue3 + Vite）

```powershell
# 推荐：脚本启动（会自动检查 node_modules，缺失时先 npm install）
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1

# 或手动
cd frontend
npm install
npm run dev
```

- 开发地址：<http://127.0.0.1:5173>，Vite 已把 `/api`、`/health` 代理到 `127.0.0.1:8000`；
- 页面：**登录 / 注册**（登录 tab 可一键填演示账号；注册 tab 自助创建学生账号）、**智能问答**（SSE 流式回答 + Agent 轨迹 + 来源引用 + 点赞点踩）、**会话历史**、
  **文档管理**（上传 `.md/.txt/.pdf`、列表、删除；仅管理员可写）、**关于与状态**（运行模式 / 图节点 / 演示要点）；
- 生产构建：`npm run build`，产物在 `frontend/dist/`；
- 前端设计说明见 [`docs/frontend-design.md`](docs/frontend-design.md)。

## 七、项目结构

```text
school-rules-rag-platform/
├── backend/
│   ├── main.py              # ✅ 极简版单文件（现场回退方案）
│   ├── demo.html            # ✅ 极简版浏览器演示页
│   ├── requirements.txt     # ✅ 依赖
│   ├── .env.example         # ✅ 环境变量模板（v2 开关 / 三库 / DeepSeek）
│   ├── app/                 # ✅ v2 正式工程
│   │   ├── main.py          #   应用工厂 + 启动预热 + /health
│   │   ├── core/            #   配置 / 密码哈希
│   │   ├── agents/          #   图引擎 + 图定义 + 11 节点 + SSE 事件
│   │   ├── api/v1/          #   chat / conversations（会话历史、反馈）
│   │   ├── services/        #   LLM / Qdrant+BGE / Neo4j / MySQL / 语义缓存适配器
│   │   ├── db/              #   SQLAlchemy 模型与引擎
│   │   └── schemas/         #   Pydantic 模型
│   ├── tests/               # ✅ 38 项测试（图引擎 + SSE + MySQL + 解析器 + PDF + Neo4j + 鉴权/注册 + 缓存/引用/OCR 回归）
│   ├── scripts/
│   │   └── ingest_rules.py  # ✅ 校规文档入库（MySQL + Qdrant + Neo4j）
│   └── data/                #   本地 Qdrant 数据（已 gitignore）
├── rules/                   # ✅ 校规文档目录（当前为虚构样例，替换为真实文件）
│   ├── 学生手册节选.md
│   └── README.md
├── frontend/                # ✅ Vue3 + Vite 前端
│   ├── src/
│   │   ├── api/             #   SSE 解析 + REST 客户端
│   │   ├── components/      #   消息气泡 / Agent 轨迹 / 来源引用
│   │   ├── stores/          #   Pinia 会话状态
│   │   └── views/           #   问答 / 历史 / 文档 / 关于
│   └── vite.config.ts       #   /api、/health 代理到 8000
├── sql/
│   └── init_mysql.sql       # ✅ 建表 + 种子数据（兼容 MySQL 5.5 / 8.0）
├── scripts/
│   ├── start_neo4j.ps1      # ✅ 启动本机 Neo4j
│   ├── start_neo4j.cmd
│   ├── start_backend.ps1    # ✅ 一键启动 v2（固定全局 Python + 真实三库 + DeepSeek）
│   └── start_frontend.ps1   # ✅ 启动 Vue3 前端（自动检查 npm install）
├── docs/
│   ├── architecture-v2.md   # ✅ v2 架构 / 图设计 / 排期
│   └── frontend-design.md   # ✅ 前端设计说明（页面 / SSE / 上传）
├── LICENSE
└── README.md
```

## 八、当前进展与规划

### ✅ 已完成

- 系统总体架构设计：MySQL + Neo4j + Qdrant 多库混合存储方案
- MySQL 数据库设计：9 张表、外键与索引、`qdrant_point_id` 映射与溯源链路；建表脚本已落地并通过 5.5.11 实测
- Neo4j 图谱本体（Ontology）设计
- **极简版多智能体闭环**：检索 / 图谱 Agent 并行召回（Mock）+ 证据融合 + 防幻觉拒答 + DeepSeek 流式生成 + MySQL Mock 落库
- **v2 正式工程（课程答辩版）**：
  - 自研 Agent 图引擎：13 节点，支持并行 / 选择 / 循环，SSE 实时输出节点轨迹；
  - 真实 Qdrant（本地嵌入式）+ 本地 BGE-small-zh 检索；
  - 真实 Neo4j 校规图谱（文档 → 章节 → 条款 → 主题），与向量检索并行双路召回；
  - 校规文档入库流水线：解析切分 → MySQL 记录 → BGE 向量 → Qdrant → Neo4j；支持命令行批量入库与 HTTP 上传接口；
  - 真实 MySQL 落库 + 会话历史 + 点赞点踩 API；
  - 真实 DeepSeek（deepseek-chat）端到端跑通；
  - PDF 文字版上传（base64）+ 扫描件 OCR 自动识别（已实测：单页约 8 秒，含「第N条」归一化）；
  - **Vue3 + Vite 前端**：登录页、SSE 流式问答、Agent 轨迹面板、来源引用、点赞点踩、会话历史、文档管理；
  - **登录鉴权 + 学生自助注册**：admin/student 演示账号、学生注册后自动登录、Bearer token、按用户隔离会话、管理员才能上传/删除文档；
  - **本轮回归修复**：降级答案不进缓存、上传/删除后清空缓存、上传前 Neo4j 预检 + 失败回滚、
    图谱证据标题引用校验、启动脚本固定全局 Python、Neo4j driver 复用、multipart 路由 OpenAPI 修复；
  - 38 项自动化测试全过

### 🚧 进行中

- 扫描件 OCR 人工校对流程（识别已可用，真实文件入库前仍需抽查）
- DOCX 解析（当前支持 `.md/.txt/.pdf`）

### 📋 后续计划

- JWT / 刷新令牌与更细的角色权限（当前为进程内 token + 管理员/学生两级）
- 证据约束生成评测（命中率、拒答准确率、响应延迟）
- 真正的问题语义缓存（embedding 相似度，当前是归一化精确匹配）
- 文档管理后台、标签、公告模块
- 真实校规文档替换 `rules/` 样例并重新入库
- 答辩材料：PPT、实验报告、演示视频

## 九、开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。
