# 公网部署（Docker Compose）

> 目标：一条 `docker compose up -d` 起完整系统（前端 + 后端 + MySQL + Neo4j + Qdrant），
> 服务器上**只对外暴露一个 HTTP 端口**（建议 80/443），数据库与向量库只在 Docker 内网。
> 本机开发仍可用 `scripts/start_backend.ps1`（Windows 模式，不需要 Docker）。

## 1. 服务与端口

| 服务 | 镜像 | 对外端口 | 说明 |
| --- | --- | --- | --- |
| frontend | nginx:1.27-alpine | `${HTTP_PORT}`（默认 8080，服务器建议 80） | 托管前端 dist，反代 `/api`、`/docs`、`/health` |
| backend | python:3.11-slim | 不暴露 | FastAPI + SSE + Agent 图 |
| mysql | mysql:8.0 | 不暴露 | 业务数据，首次启动执行 `sql/init_mysql.sql` |
| neo4j | neo4j:5.26 | 不暴露 | 校规知识图谱（内网 auth=none） |
| qdrant | qdrant/qdrant | 不暴露 | 向量库（服务端模式，替代本机嵌入式） |

> Qdrant 本地嵌入式模式与服务器模式的数据不互通，切换到 Docker 后需要重新执行一次入库。

## 2. 服务器要求

- 2 核 4G 起步（BGE + Neo4j + MySQL 同时跑，内存偏紧），推荐 **4 核 8G**；
- Ubuntu 22.04/24.04 或同类 Linux；
- 已安装 Docker Engine 24+ 与 Docker Compose v2；
- 磁盘 ≥ 20G（镜像 + 模型 + 数据）；
- 公网服务器安全组/防火墙只开放 `22`、`80`、`443`。

## 3. 首次部署

```bash
git clone https://github.com/xc-0525/school-rules-rag-platform.git
cd school-rules-rag-platform

# 1) 准备环境变量（不要提交 .env）
cp .env.docker.example .env
vi .env
#   DEEPSEEK_API_KEY=sk-...
#   MYSQL_ROOT_PASSWORD=强密码
#   HTTP_PORT=80            # 服务器直接对外 80；本地测试保持 8080
#   AUTH_REQUIRED=1

# 2) 下载 BGE 模型到 ./models（profile=init，不会随默认 up 启动）
docker compose run --rm model-download
#   国内服务器网络慢时：在 .env 里加 HF_ENDPOINT=https://hf-mirror.com

# 3) 构建并启动
docker compose up -d --build

# 4) 首次入库校规语料（后端已启动，Qdrant/Neo4j/MySQL 就绪后执行）
docker compose run --rm ingest

# 5) 检查（/health 返回 200；/ready 在 BGE 预热完成后返回 200）
curl http://127.0.0.1/health
curl http://127.0.0.1/ready
# 浏览器打开 http://服务器IP/（或你配置的域名）
```

登录：管理员/学生演示账号见交付说明（初始密码不在仓库里；上线前请修改）。

## 4. 本机先用 Docker 试跑（Windows）

1. 启动 Docker Desktop，确认 `docker info` 正常；
2. 本地模型已经在 `G:\ai-models\...`，可直接复制，避免重新下载：

```powershell
New-Item -ItemType Directory -Force .\models\bge-small-zh-v1.5 | Out-Null
Copy-Item -Recurse -Force "G:\ai-models\hf\hub\models--BAAI--bge-small-zh-v1.5\snapshots\7999e1d3359715c523056ef9478215996d62a620\*" .\models\bge-small-zh-v1.5\
```

3. 准备 `.env`（`HTTP_PORT=8080`），然后：

```powershell
docker compose up -d --build
docker compose run --rm ingest
# 浏览器：http://127.0.0.1:8080
```

如果不想在 Windows 上构建，也可以只在服务器执行；本地继续用 `start_backend.ps1`。

## 5. 公网 HTTPS（推荐 Caddy）

最简单的做法：在服务器上用 Caddy 做 TLS 终止，反代到 frontend 容器：

```caddyfile
# /etc/caddy/Caddyfile
your-domain.com {
    encode gzip
    reverse_proxy 127.0.0.1:8080
}
```

- DNS：域名 A 记录指向服务器公网 IP；
- 防火墙：只开放 22/80/443；
- 把 compose 的 `HTTP_PORT` 改为 `127.0.0.1:8080` 更安全（只允许 Caddy 访问）：
  ```yaml
  ports:
    - "127.0.0.1:8080:80"
  ```
- Caddy 自动申请 Let's Encrypt 证书；登录 token 走 HTTPS。

## 6. 更新与维护

```bash
git pull
docker compose build backend frontend
docker compose up -d
# 语料有更新时重新入库（会清空旧文档后重建）
docker compose run --rm ingest
```

备份：

```bash
docker compose exec mysql mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" school_rules_rag > backup_$(date +%F).sql
docker run --rm -v school-rules-rag_qdrant_data:/data -v "$PWD":/backup alpine tar czf /backup/qdrant_$(date +%F).tgz -C /data .
docker run --rm -v school-rules-rag_neo4j_data:/data -v "$PWD":/backup alpine tar czf /backup/neo4j_$(date +%F).tgz -C /data .
```

查看日志：

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

停止：

```bash
docker compose down          # 保留数据卷
docker compose down -v       # 连数据一起删除（慎用）
```

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| backend 一直 unhealthy | 首次 BGE 加载需 2~3 分钟；看 `docker compose logs backend`，确认 `EMBEDDING_MODEL_PATH` 下模型文件存在 |
| 模型下载失败 | 设置 `HF_ENDPOINT=https://hf-mirror.com`，或把本机模型 scp 到服务器 `models/bge-small-zh-v1.5` |
| 上传文档 503 / 无法入库 | 确认 Redis？不需要；确认 Neo4j/Qdrant/MySQL 容器都在运行，看 backend 日志 |
| Qdrant 没有数据 | Docker 模式必须重新执行 `docker compose run --rm ingest`，本机嵌入式数据不会自动迁移 |
| 前端 502 | backend 还没 healthy；`docker compose ps` 查看状态 |
| SSE 一次性显示 | 检查是否经过了会缓冲的代理；本项目的 nginx 配置已设置 `proxy_buffering off` |
| 内存不足 | 调小 Neo4j 的 `NEO4J_server_memory_heap_max__size`，或升级到 4C8G |

## 8. 安全清单

- `.env` 不进仓库；`MYSQL_ROOT_PASSWORD` 用强密码；
- 只暴露 frontend 端口，MySQL/Neo4j/Qdrant/backend 不映射到公网；
- `AUTH_REQUIRED=1`；上线前修改演示账号密码；
- 服务器防火墙只开 22/80/443；
- 定期备份 MySQL / Neo4j / Qdrant 数据卷。
