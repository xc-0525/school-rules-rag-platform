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
- **高频问题语义缓存**：对高频问题做语义级缓存，命中即返回，显著降低 DeepSeek 调用延迟与成本。
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

> 建表脚本：`sql/init_mysql.sql`（MySQL 8.0+，utf8mb4 / InnoDB，含外键级联策略与索引设计）。

### 5.4 初始化数据

- 默认管理员账号：`admin` / `admin123`（bcrypt 哈希存储，**上线前请务必修改密码**）
- 默认文档标签：学籍管理、违纪处分、奖助学金、宿舍管理、教学管理、校园安全

## 六、快速开始

> 项目处于开发阶段，以下环境要求与启动方式以最终代码为准。

### 6.1 环境要求

- Python 3.10+
- Node.js 18+
- MySQL 8.0+
- Neo4j 5.x
- Qdrant 1.x（推荐 Docker 部署）
- 本地 BGE-small-zh 模型
- DeepSeek API Key

### 6.2 初始化 MySQL

```bash
# 创建数据库 school_rules_rag 并建表、写入初始数据
mysql -u root -p < sql/init_mysql.sql
```

### 6.3 启动 Qdrant / Neo4j（Docker）

```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 qdrant/qdrant
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/yourpassword neo4j:5
```

### 6.4 后端配置（`.env`）

```env
MYSQL_URL=mysql+pymysql://root:password@127.0.0.1:3306/school_rules_rag
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=yourpassword
QDRANT_URL=http://127.0.0.1:6333
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 6.5 前端启动

```bash
cd frontend
npm install
npm run dev
```

## 七、项目结构（规划）

```text
school-rules-rag-platform/
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── agents/         # 检索 / 图谱 / 生成智能体与编排
│   │   ├── api/            # 路由（鉴权、会话、文档、问答 SSE）
│   │   ├── core/           # 配置、安全、日志
│   │   ├── db/             # MySQL / Qdrant / Neo4j 连接管理
│   │   ├── models/         # SQLAlchemy ORM 模型
│   │   ├── schemas/        # Pydantic 模型
│   │   └── services/       # 文档解析切分、向量入库、语义缓存
│   └── requirements.txt
├── frontend/               # Vue3 前端
├── sql/
│   └── init_mysql.sql      # MySQL 建表与初始化脚本
├── LICENSE
└── README.md
```

## 八、当前进展与规划

### ✅ 已完成

- 系统总体架构设计：MySQL + Neo4j + Qdrant 多库混合存储方案
- MySQL 数据库设计：9 张建表脚本、外键与索引、ID 映射与溯源链路
- Neo4j 图谱本体（Ontology）设计

### 🚧 进行中

- 多智能体编排："检索 - 图谱 - 生成"工作流
- 文档解析切分与 BGE-small-zh 向量入库
- 打通"图谱 + 向量"双路召回链路

### 📋 后续计划

- 证据约束生成、无证据拒答与人工引导
- 高频问题语义缓存
- Vue3 前端与 SSE 流式问答、来源引用展示
- 文档管理后台、反馈、标签与公告模块
- 问答质量评测（命中率、拒答准确率、响应延迟）

## 九、开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。
