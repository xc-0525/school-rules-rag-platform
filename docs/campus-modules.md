# 校园服务模块设计（报修 / 预约 / 失物招领 / 文档预览）

> 实现分支：`feat/campus-service-modules`。
> 四个模块互相独立、单独可回退，共同遵守 `docs/platform-extension.md` 第 3 节的扩展约定：
> `services/<key>.py`（业务逻辑，MySQL + 内存双实现）→ `api/v1/<key>.py`（`build_router`）→
> `main.py` 注册一行。后续接入问答只需在 `agents/tools.py` 的 `TOOL_DEFINITIONS` 加一条、
> `_dispatch` 接一行，不动核心链路。

## 0. 总览

| 模块 | 后端服务 | 数据表 | 关键机制 |
| --- | --- | --- | --- |
| 文档在线预览 | `api/v1/documents.py`（新增 preview 路由） | 复用 `documents` | 类型白名单 + 目录穿越防御 |
| 寝室报修 | `services/repairs.py` | `repair_requests` | 每日限额 + 同位同型判重 |
| 图书馆预约 | `services/reservations.py` | `reservable_locations` + `location_reservations` | 行锁 + 容量计数（不再用唯一键） |
| 失物招领 | `services/lostfound.py` | `lost_found_items` | 先审后发 + 图片白名单校验 |

迁移：`sql/migrations/004_campus_services.sql`（全部 `CREATE TABLE IF NOT EXISTS`，可重复执行），
幂等脚本 `backend/scripts/apply_campus_service_migration.py`。

---

## 1. 文档在线预览

### 1.1 方案

学生和管理员在文档管理页点击文件名，以**只读**方式在线预览原件：

- 前端 `fetch`（携带 Authorization 头）取回字节流 → `URL.createObjectURL` → 新窗口打开；
  这样避免了在 URL 上拼 token（不进浏览器历史 / 访问日志）。
- 后端返回 `Content-Disposition: inline` + `X-Content-Type-Options: nosniff`。

### 1.2 支持类型（浏览器原生渲染，无需转换服务）

| 类型 | MIME | 说明 |
| --- | --- | --- |
| `.pdf` | `application/pdf` | 浏览器内嵌阅读器 |
| `.png / .jpg / .webp / .gif` | `image/*` | 直接渲染 |
| `.txt / .md` | `text/plain` / `text/markdown; charset=utf-8` | 按纯文本展示 |
| 其余（doc/docx/xls 等） | — | 返回 **415**，前端弹窗提示"下载后查看" |

不做 Office 转 HTML/PDF：引入转换服务（LibreOffice headless 等）的部署成本与评审讲清收益不成比例，
415 + 明确提示是当前规模的合理边界，后续需要时新增 `preview_converters` 扩展点即可。

### 1.3 权限与安全

- **权限**：登录用户（学生与管理员）均可只读预览；删除仍仅管理员，与现状一致。
- **目录穿越防御**：`file_path` 取自数据库，接口会校验 `resolve()` 后仍位于
  `data/uploads/` 内，防止数据库记录被篡改后读到任意文件。
- 404 区分两种情况：文档记录不存在 / 原件文件已被清理。

---

## 2. 寝室报修

### 2.1 数据模型 `repair_requests`

| 字段 | 说明 |
| --- | --- |
| `user_id` | 申请人 |
| `title / description / category` | 问题与分类（water_power/network/furniture/door_window/other） |
| `building / room / contact` | 楼栋、房间、联系方式 |
| `status` | `pending → processing → completed`；`pending/processing → closed` |
| `handler_id / handler_remark` | 受理人与处理意见 |

**为什么不用 tickets 表**：状态机（投诉单无"processing/completed"语义）、限流规则、
字段完全不同；复用会让 tickets 承担两个职责。两表独立演进互不影响。

### 2.2 限流与重复判定（两层，服务层实现，配置在 Settings）

1. **频率限制（429）**：同一用户每滚动 24 小时最多 `REPAIR_PER_USER_PER_DAY`（默认 3）条。
2. **重复判定（409）**：同一用户 + 同楼栋 + 同房间 + 同类别，
   `REPAIR_DUPLICATE_HOURS`（默认 24h）窗口内已存在**任意状态**的申请即拒绝——
   "刚提交怕没收到"式的重复轰炸在窗口期直接拦下；换房间或换类别不受影响。

两个口径在 MySQL 实现与内存实现中完全一致（测试逐条对齐）。

### 2.3 接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/repairs` | 登录 | 提交申请；429=超频，409=重复，400=非法输入 |
| GET | `/api/v1/repairs?status=` | 登录 | 学生=本人列表；管理员=全部 |
| GET | `/api/v1/repairs/{id}` | 属主/管理员 | 学生看别人的返回 404（不暴露存在性） |
| POST | `/api/v1/repairs/{id}/transition` | 见下 | `{action: process\|complete\|close\|cancel, remark}` |

流转权限：`process/complete/close` 仅管理员（close 必须附原因）；`cancel` 仅本人且仅 pending。

### 2.4 边界情况

- 非法输入（空标题、<5 字描述、空联系方式、非法分类）→ 400，中文错误信息直达前端；
- 非法流转（如 completed 再 close）→ 400；已结束工单不可再动；
- 学生取消已被受理的单 → 400（提示学生联系后勤关闭）。

---

## 3. 图书馆位置预约

### 3.1 数据模型

- `reservable_locations`：位置（名称/分区/**容量**/是否开放），迁移自带 4 条种子数据。
  容量语义：自习区 40、电子阅览 30（按座位数）；研讨间 1（按整间）。
- `location_reservations`：`(location_id, res_date, slot)` + `status`；
  一个时段可被多人预约，直到占满 `capacity`（迁移 006 移除了早期的一人独占唯一键）。
- 时段为固定三段：`morning / afternoon / evening`（扩展新时段改枚举表即可）。

### 3.2 时间规则

**只允许预约「明天」起 `RESERVATION_DAYS_AHEAD`（默认 7）天内的时段**；
日期范围由服务端本地时钟计算，客户端传参只做格式与范围校验（今天/过去/超范围 → 400）。

### 3.3 并发控制（按容量占用）

1. **事务内行锁**：`SELECT ... FROM reservable_locations WHERE id=? FOR UPDATE`
   锁住位置行 → 锁内 `COUNT` 该 (location, date, slot) 的有效预约数 → 未满 `capacity` 才插入。
   并发请求在锁上串行化，计数与插入不可分割，不会超卖；
2. **兜底**：`INSERT` 异常统一回滚并映射为 409（不再依赖唯一键，因为一个时段允许多人）；
3. **同人不重复**：同一用户在同一位置同一时段已有有效预约时直接 409，避免白占名额。

`GET /reservations/availability` 返回每个时段的 `capacity / booked_count / remaining`，
`booked` 字段保留为“是否已满”的布尔兼容值。

**过期与取消**：`is_expired` 标记“日期已过但仍为 reserved”的记录，前端默认折叠、可展开；
管理员可代取消任意用户的预约，取消时必须填写原因（写入 `cancel_reason`），
学生在“我的预约”直接看到该原因——作为一次定向通知，不额外引入站内信表。

**冲突处理**：统一捕获后映射为 **HTTP 409**（detail 含 `message` 与 `conflict_reservation_id`），
前端提示"手慢了，该时段刚被别人预约成功"，并自动刷新时段占用表；绝不把 500 暴露给用户。

### 3.4 接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/reservations/locations` | 登录 | 位置列表 |
| GET | `/api/v1/reservations/availability?location_id=&date=` | 登录 | 各时段占用情况 |
| POST | `/api/v1/reservations` | 登录 | `{location_id, res_date, slot}`；201/409/400/429 |
| GET | `/api/v1/reservations` | 登录 | 我的预约 |
| POST | `/api/v1/reservations/{id}/cancel` | 属主/管理员 | 取消 |
| POST | `/api/v1/reservations/{id}/fulfill`、`.../no_show` | 管理员 | 核销 / 违约登记 |

### 3.5 边界情况

- 每人同时有效预约上限 `RESERVATION_MAX_ACTIVE_PER_USER`（默认 3）→ 429；
- 取消后时段立即释放（availability 只统计 `reserved`）；
- 违约（no_show）记录已落库，后续可按用户统计连续违约次数限制预约（扩展点，未启用）；
- 内存实现用 `RLock` 保证「检查+插入」原子性（测试里 8 线程并发抢同一段位，恰好 1 个成功）。

---

## 4. 失物招领

### 4.1 数据模型 `lost_found_items`

| 字段 | 说明 |
| --- | --- |
| `kind` | `lost`（我丢了东西）/ `found`（我捡到东西） |
| `title / description / location / contact` | 内容（联系方式建议打码，审核环节把关） |
| `image_urls` | JSON 数组，存上传接口返回的相对路径 |
| `status` | 见下 |
| `reviewer_id / review_remark / reviewed_at` | 审批留痕 |

### 4.2 状态流转

```
pending_review ──approve──▶ published ──resolve(发布人/管理员)──▶ resolved
      │
      └──reject(必须附理由)──▶ rejected        published ──remove(管理员)──▶ removed
```

- 学生提交 → `pending_review`：公列表**不可见**（`GET ?scope=public` 只返回 published），
  仅"我的发布"里可见并能看到驳回理由；
- rejected 是终态：需修改后重新提交（比"打回待改"少一个状态，行为更可预期）；
- 发布人确认找回/归还 → `resolved`，从公示栏消失。

### 4.3 图片上传

| 项 | 规则 |
| --- | --- |
| 格式 | jpg / png / webp（后缀与 `Content-Type` 双重校验，不一致 → 400） |
| 大小 | 单张 ≤ `LOSTFOUND_IMAGE_MAX_MB`（默认 5MB），超限 → 413 |
| 数量 | 每条 ≤ `LOSTFOUND_MAX_IMAGES`（默认 3 张） |
| 存储 | `data/lostfound/<uuid32>.<ext>`，随机文件名 |
| 读取 | `GET /lost-found/images/{filename}`，文件名白名单正则（32 位 hex + 单后缀）杜绝路径穿越；登录用户可读 |
| 引用 | 发布时只接受 `/api/v1/lost-found/images/` 开头的相对路径，外链/穿越串 → 400 |

前端 `<img>` 无法带 Authorization 头，`SecureImage` 组件用 fetch 取 blob 转 objectURL 展示。

### 4.4 接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/lost-found/images` | 登录 | multipart 上传，返回 `{url, size_bytes, content_type}` |
| POST | `/api/v1/lost-found` | 登录 | 提交发布申请 → 201 pending_review |
| GET | `/api/v1/lost-found?scope=public\|mine\|pending&kind=` | 登录；`pending` 仅管理员 | 列表 |
| GET | `/api/v1/lost-found/{id}` | 已发布=登录；未发布=属主/管理员 | 详情 |
| POST | `/api/v1/lost-found/{id}/review` | 管理员 | `{action: approve\|reject\|remove, remark}` |
| POST | `/api/v1/lost-found/{id}/resolve` | 发布人/管理员 | 确认解决 |

### 4.5 审批超时

不做后台定时任务（部署面最小）：`GET ?scope=pending&older_than_hours=48` 由管理端主动查询，
前端在"我的发布"里对超 48h 未审条目显示提醒条（`LOSTFOUND_REVIEW_TIMEOUT_HOURS` 可配）。
接入消息推送/定时催办时直接复用同一查询，无需改表。

---

## 5. 权限控制总表

| 操作 | 匿名 | 学生 | 管理员 |
| --- | --- | --- | --- |
| 文档预览 | 401 | ✅ | ✅ |
| 报修提交/取消本人 | 401 | ✅（限流内） | ✅ |
| 报修受理/完成/关闭 | 401 | 403 | ✅ |
| 预约/取消本人 | 401 | ✅ | ✅ |
| 预约核销/违约登记 | 401 | 403 | ✅ |
| 失物发布申请/确认解决 | 401 | ✅ | ✅ |
| 失物审批/下架/待审列表 | 401 | 403 | ✅ |

实现口径与既有模块一致：`build_current_user_dependency(settings)` + `user.is_admin`；
服务层再做属主二次校验（接口层绕过时兜底），学生访问他人资源统一 404 而非 403，不暴露存在性。

## 6. 测试

`backend/tests/test_campus_services.py`（21 项）覆盖：
限流/判重/状态机/属主隔离、日期越界、并发抢位（8 线程恰好 1 成功）、配额上限、
审核流全路径、图片 URL 白名单、接口层 409/429/403 映射。
全量后端测试 308 项（307 通过、1 项按环境跳过）。

## 7. 问答接入（已实现，见 docs/工具接入改造指南.md）

**读**：`query_repair_status` / `query_my_reservations` / `query_location_availability` /
`query_lost_found` / `query_my_lost_found`。

**写**：一律走"草稿 + 用户确认"，模型不直接落库。
每个写操作一个独立工具、一个独立确认接口，互不复用：

| 工具 | 业务动作 | 用户确认后调用 | 落库状态 |
| --- | --- | --- | --- |
| `create_reservation_draft` | 预约场馆 | `POST /api/v1/reservations` | reserved |
| `create_lost_found_draft` | 发布寻物/招领 | `POST /api/v1/lost-found` | pending_review |
| `create_repair_draft` | 提交寝室报修 | `POST /api/v1/repairs` | pending |
| `cancel_reservation_draft` | 取消预约 | `POST /api/v1/reservations/{id}/cancel` | cancelled |
| `resolve_lost_found_draft` | 失物结案 | `POST /api/v1/lost-found/{id}/resolve` | resolved |

草稿阶段只做校验（复用服务层 `validate_payload` / `precheck_limits`，与真实写入同一套规则），
不写任何数据；工具返回 `requires_confirmation=true` + `action`，前端按 action 渲染对应确认按钮。

消融开关 `CAMPUS_TOOLS_ENABLED`（默认 1）同时作用于三处，保证对照组干净：
① 不下发工具定义；② 系统提示词裁掉校园工具清单与使用规则；③ 执行层拦截（模型幻觉调用也返回失败）。

评测脚本新增 `--with-tools`，配套题集 `tests/golden/tool_set.json`（8 题）实测结果：

| 配置 | 工具选对率 | RAG 对照题命中 | 被拒答题数 | p50 时延 |
| --- | --- | --- | --- | --- |
| 开启校园工具 | 1.000 | 1.000 | 0 | 3512 ms |
| 关闭校园工具 | 0.125 | 1.000 | 4 | 3245 ms |

> 上表为"5 个只读工具"版本测得的基线数据；后续新增的 5 个写操作草稿工具不改变
> 该题集的判定口径（题集只考查询类问题），其正确性由 `tests/test_tools_campus.py`
> 的 23 项用例覆盖（草稿不落库、必填校验、限流预检、归属校验）。

关闭组 0.125 只来自 t08（RAG 对照题），说明 7 道实时业务题在无工具时全部拿不到数据；
两组 RAG 对照题命中率均为 1.000，证明接入工具没有抢走 `search_rules` 的调用。

## 8. 后续扩展点（已预留，未实现）

- **违约限制**：`location_reservations` 已按用户落 no_show 记录，加一个计数查询即可实现"连续违约 N 次禁约"；
- **报修图片**：`repair_requests` 预留 `handler_remark`，如需故障照片可复用失物招领的上传链路；
- **消息通知**：三个模块的状态变更点（受理/完成/审批/冲突）都集中在 service 函数内，
  接站内信/邮件时只需在这些点发事件。
