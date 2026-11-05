# 2 核 4G 试用机部署清单（腾讯云 CVM 蜂驰型 2C4G3M）

> 适用：7 天免费试用机 / 轻量 2C4G 同档位。4C8G 机型不需要本文，直接用 `docs/deploy-public.md`。
> 结论：**能跑**，但默认配置会 OOM，必须叠加 `docker-compose.4g.yml` + 宿主机 swap。

## 0. 资源账（为什么必须调）

| 服务 | 默认配置实占 | 本文调优后 |
| --- | --- | --- |
| Neo4j | ~1.8 G（heap 1G + pagecache 512M） | ~0.9 G（heap 512M + pagecache 128M） |
| backend（torch + BGE） | ~1.2–1.8 G | ~1.2 G（限制 1.6 G + 2 线程） |
| MySQL | ~0.5 G | ~0.35 G（buffer pool 128M、关 performance_schema） |
| Qdrant | ~0.3 G | ~0.25 G |
| nginx | ~0.03 G | ~0.03 G |
| **合计** | **≈ 4–5 G（超了）** | **≈ 3.56 G（限流总和）+ swap 兜底** |

## 1. 开机第一步：加 2G swap（不做这步后面必炸）

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # 确认 Swap 一列有 2.0Gi
```

顺带把 swap 倾向调低一点，避免频繁换页拖慢 BGE 推理：

```bash
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf && sudo sysctl -p
```

## 2. 装 Docker + 配镜像加速（腾讯云机器用内网源）

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://mirror.ccs.tencentyun.com"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
```

> `log-opts` 很关键：50G 系统盘，日志不限制几个月就能写满。

## 3. 拉代码 + 填 .env

```bash
git clone https://github.com/xc-0525/school-rules-rag-platform.git
cd school-rules-rag-platform
cp .env.docker.example .env
```

`.env` 里必改这四项：

```ini
HTTP_PORT=80
MYSQL_ROOT_PASSWORD=<强密码>
AUTH_TOKEN_SECRET=<32 位随机串>      # 不填会导致重启后所有人掉线
DEEPSEEK_API_KEY=<你的 key>
HF_ENDPOINT=https://hf-mirror.com     # 3M 带宽下不下镜像，模型可能拉半小时
```

> 只用 `IP:80` 访问不需要备案；绑域名走 80/443 则必须先 ICP 备案。

## 4. 下载模型（只做一次，约 100 MB）

```bash
docker compose run --rm model-download
```

如果卡住：确认 `.env` 里 `HF_ENDPOINT` 已生效，或从本机 `scp -r models/bge-small-zh-v1.5 root@<IP>:~/school-rules-rag-platform/models/`。

## 5. 启动（注意必须带两个 -f）

```bash
docker compose -f docker-compose.yml -f docker-compose.4g.yml up -d --build
```

首次构建要装 CPU 版 torch，**20–40 分钟**（2 核 + 3M 带宽），不要中途 Ctrl+C。
`backend` 的 healthcheck 有 300s 预热窗口，BGE 没加载完显示 `unhealthy` 是正常的。

## 6. 首次入库（否则问答全空）

```bash
docker compose -f docker-compose.yml -f docker-compose.4g.yml run --rm ingest
```

21 篇语料 + 向量化，2 核机约 3–8 分钟。

## 7. 上线自检（五条，全过才算成功）

```bash
docker compose ps                                   # backend 应为 healthy
curl -s localhost/health | head -c 200              # 看 vector_ready / graph 模式
docker stats --no-stream                            # 各容器内存是否逼近 limit
curl -s -X POST localhost/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | head -c 120
```

浏览器打开 `http://<公网IP>/`，登录后问一句「宿舍晚上几点熄灯」，能出带来源的回答即可。

## 8. 2C4G 上会遇到的三个现象（不是 bug）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 首屏 4–6 秒 | 3M 带宽 ≈ 375 KB/s，主包 1.1 MB | 已开 gzip（约 350 KB），仍慢就接受，SSE 正文本身很小 |
| 第一次提问等 10–20 秒 | BGE 冷启动 + 2 核编码 | 之后单次 2–5 秒；答辩前先问一句预热 |
| 传 20MB PDF 很慢 | 上行也是 3M | 演示用小 PDF（<2MB），或本机先入库再导数据 |

## 9. 内存还是不够时的降级顺序

1. Neo4j `pagecache` 128M → 64M（改 `docker-compose.4g.yml`）
2. Neo4j `heap_max` 512M → 384M
3. backend `mem_limit` 1.6G → 1.4G（观察是否被 OOM kill：`docker inspect <容器> | grep OOMKilled`）
4. 最后手段：`GRAPH_MODE=mock` 关掉 Neo4j 图检索（省 ~0.9G，代价是召回从 0.85 降到 0.80，演示可接受）

## 10. 7 天试用期的安排建议

- **第 1 天**：按本文跑通，确认五条自检全过；
- **第 2–3 天**：真实数据入库 + 走一遍演示脚本（登录→问答→发布失物→预约→报修）；
- **第 5 天**：做一次冷重启（`docker compose down && up -d`），确认数据卷持久化没问题；
- **答辩前 1 天**：不要再改配置，只预热 + 截图备份。

到期前如果想留数据：

```bash
docker compose exec mysql mysqldump -uroot -p school_rules_rag > backup.sql
docker run --rm -v school-rules-rag-platform_qdrant_data:/q -v $PWD:/out alpine \
  tar czf /out/qdrant_data.tgz -C /q .
```

换到 4C8G 机器时**不要带 `docker-compose.4g.yml`**，直接用默认配置，再把 `backup.sql` 导回去即可。
