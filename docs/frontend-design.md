# 前端设计说明（Vue3 + Vite）

> 对应代码：`frontend/`；后端：`backend/app`（FastAPI，默认 8000）。
> 启动：`scripts/start_frontend.ps1`（或 `cd frontend && npm install && npm run dev`）。

## 1. 技术选型

| 层 | 选择 | 理由 |
| --- | --- | --- |
| 框架 | Vue 3（`<script setup>`） | 课程要求 + 生态成熟、上手快 |
| 构建 | Vite 6 | 启动快，自带的 dev proxy 解决跨域 |
| UI | Element Plus | 表格、对话框、时间线等组件齐全，答辩界面体面 |
| 状态 | Pinia | 会话状态（conversation_id / messages）跨页面共享 |
| 路由 | Vue Router 4 | 四个页面 + history 模式 |
| 渲染 | markdown-it（关闭 raw HTML） | 大模型输出 Markdown 安全渲染 |
| 流式 | 原生 `fetch` + `ReadableStream` 解析 SSE | 不引入额外 SDK，事件与后端一一对应 |

## 2. 页面与能力

| 路由 | 页面 | 能力 |
| --- | --- | --- |
| `/chat` | 智能问答 | SSE 流式回答；Agent 轨迹时间线；来源引用卡片；点赞/点踩；新对话；缓存/拒答/降级标签 |
| `/history` | 会话历史 | 会话列表、消息详情、继续对话 |
| `/documents` | 文档管理 | 上传 `.md/.txt/.pdf`（文本或 base64 JSON）、列表、删除、入库结果提示 |
| `/about` | 关于与状态 | `/health` 运行模式、Agent 图节点/控制结构、接口一览、演示要点 |

## 3. SSE 事件映射

后端 `POST /api/v1/chat`（`stream=true`）返回：

| 事件 | 前端处理 |
| --- | --- |
| `meta` | 设置来源列表、缓存命中标记 |
| `trace` | 追加到当前回答的轨迹数组，驱动时间线 |
| `delta` | 追加到回答正文（打字机效果） |
| `done` | 写入最终答案、来源、耗时、runtime（步数/检索轮数/重生成次数）；刷新 `conversation_id` 并回填消息 ID |
| `error` | 显示错误信息，结束 pending 状态 |

`frontend/src/api/sse.ts` 负责按 `\n\n` 切分事件帧、`event:`/`data:` 解析，不依赖第三方库。

## 4. Agent 轨迹面板

`AgentTrace.vue` 把 `trace` 事件还原成可读时间线：

- 节点 `start/end` → 「运行中 → 完成 + 耗时」；
- `route` → 「选择 / 循环」标签 + `from → to（原因）`；
- 顶部汇总 `steps`、`retrieval_round`、`regen_count`；
- 对 `parallel` / `selection` / `loop` 控制结构分别打标签，答辩时可直接对照图设计讲。

## 5. 文档上传（不依赖 python-multipart）

- `.md/.txt`：前端 `File.text()` → `content` 字段；
- `.pdf`：`File.arrayBuffer()` → base64 → `content_base64` 字段；
- 统一走 `POST /api/v1/documents/upload`（JSON），因此**不需要安装 python-multipart**；
- 若后端安装了 python-multipart，会额外出现 `/documents/upload-file`，但前端不依赖它；
- 上传需要后端真实模式（`VECTOR_MODE=qdrant` + Neo4j 已启动）；失败会显示后端返回的具体原因
  （如 Neo4j 不可用、扫描件需要 OCR）。

## 6. 目录结构

```text
frontend/
├── index.html
├── vite.config.ts          # /api、/health 代理到 127.0.0.1:8000
├── src/
│   ├── main.ts / App.vue
│   ├── styles.css
│   ├── types.ts
│   ├── api/client.ts       # REST 客户端
│   ├── api/sse.ts          # SSE 流解析
│   ├── stores/chat.ts      # Pinia：会话与消息
│   ├── components/
│   │   ├── AgentTrace.vue
│   │   ├── MessageBubble.vue
│   │   └── SourceList.vue
│   └── views/
│       ├── ChatView.vue
│       ├── HistoryView.vue
│       ├── DocumentsView.vue
│       └── AboutView.vue
```

## 7. 已知限制

- 暂无登录页/鉴权：后端固定 `user_id=1`，前端直接进入问答（登录列在后续计划）；
- 会话历史无删除入口（后端暂未提供删除会话接口）；
- 生产构建包较大（Element Plus 全量引入），课程演示用 dev server 即可；
- 演示前请确保后端 `/health` 的 `vector_ready=true`，否则首个问题会等待 BGE 预热。
