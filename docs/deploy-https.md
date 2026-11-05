# 域名 + HTTPS 配置手册（百度云 2C4G / Docker Compose）

> 目标：把 `http://<公网IP>:8080` 变成 `https://rules.你的域名.top`，可以写进简历。
> 前置：`docs/deploy-trial-2c4g.md` 已跑通，`docker compose ps` 五个服务都是 healthy。

## 0. 先做决定：备案还是免备案

国内服务器（百度云/阿里云/腾讯云）用 **80/443 端口对公网提供网站服务，必须 ICP 备案**，否则会被运营商拦截。三条路：

| 方案 | 耗时 | 钱 | 简历 URL 长什么样 | 适用 |
|---|---|---|---|---|
| **A. 备案 + Let's Encrypt** | 7–20 工作日 | 域名 ¥10–30/年 | `https://rules.xxx.top` | ✅ 推荐，长期放简历 |
| **B. Cloudflare 代理（免备案）** | 30 分钟 | 同上 | `https://rules.xxx.top` | 赶时间，但国内访问慢 |
| **C. 非标端口 + DNS 证书** | 1 小时 | 同上 | `https://rules.xxx.top:8443` | 不想备案又要直连 |

**你的情况选 A**：答辩在明年 5–6 月，现在备案时间完全够。B/C 是备选。

> ⚠️ 备案期间 80/443 不能对外服务，但 8080 端口不受影响，演示照常。

---

## 1. 买域名

**在哪买**：腾讯云 / 阿里云都行（`.cn` 备案最顺，`.top` 最便宜首年 ¥9）。不建议在 GoDaddy 等海外注册商买——国内备案要提交域名证书，海外注册商拿证书麻烦。

**买完必须做两件事**：

1. **实名认证**（国内注册商强制，1–3 个工作日，不认证域名会被 hold）
2. **确认域名有效期 ≥ 3 个月**（备案硬性要求）

## 2. 备案（方案 A）

百度智能云控制台 → 备案 → 新增备案。

**材料**：身份证正反面、人脸核验、域名证书、真实性核验单（系统生成后打印签字）。

**周期**：百度云初审 1–2 天 → 管局审核 1–20 个工作日（各省不同，湖北/江苏偏慢）。

**网站信息怎么填**（这几个坑会直接被驳回）：

| 字段 | 建议填法 | 不能填 |
|---|---|---|
| 网站名称 | `校园服务问答` / `校规查询助手` | 含「大学」「学院」「中国」「国家」会被驳回；纯英文/纯数字也不行 |
| 网站服务内容 | `其他` 或 `教育` | 选「论坛」「新闻」需要前置审批，个人办不下来 |
| 备注 | `个人毕业设计项目，仅用于技术演示，不涉及经营` | 空着 |

**备案期间**：80/443 会被拦，域名访问会跳到备案提示页。用 `http://IP:8080` 继续演示。

## 3. 域名解析

备案通过后，在 DNS 控制台加一条 A 记录：

```
主机记录: rules        （即 rules.xxx.top，想用主域名就填 @）
记录类型: A
记录值:   你的公网 IP
TTL:      600
```

验证（在你本机执行，不是服务器）：

```bash
nslookup rules.xxx.top
# 返回的 Address 应该等于你的公网 IP
```

## 4. 申请证书（acme.sh + webroot，不停服务）

选 acme.sh 而不是 certbot：单文件、不依赖 Python 环境、自动续期更省心。

**登录服务器**：

```bash
# 1. 装 acme.sh
curl https://get.acme.sh | sh -s email=你的邮箱@qq.com
source ~/.bashrc

# 2. 用 Let's Encrypt（默认是 ZeroSSL，大陆访问偶尔不稳）
acme.sh --set-default-ca --server letsencrypt

# 3. 进项目目录
cd /opt/school-rules-rag-platform     # ← 换成你的实际路径
mkdir -p certbot/www certs            # webroot 目录必须提前存在，acme.sh 不会自动建

# 4. webroot 模式申请（nginx 已配好 /.well-known/acme-challenge/ 路由）
#    注意用绝对路径，acme.sh 内部不会帮你展开 ./
acme.sh --issue -d rules.xxx.top --webroot "$PWD/certbot/www"
```

看到 `Cert success` 就成功了。证书在 `~/.acme.sh/rules.xxx.top/`。

**如果报 `Invalid response` / 404**：说明 80 端口没通，先确认

```bash
curl -I http://rules.xxx.top/            # 应该返回 200 或 301，不能超时
docker compose ps frontend                # 必须是 Up
```

## 5. 安装证书到项目目录

```bash
mkdir -p certs certbot/www

acme.sh --install-cert -d rules.xxx.top \
  --key-file       "$PWD/certs/privkey.pem" \
  --fullchain-file "$PWD/certs/fullchain.pem" \
  --reloadcmd      "docker compose -f docker-compose.yml -f docker-compose.4g.yml -f docker-compose.https.yml exec -T frontend nginx -s reload"
```

`--reloadcmd` 是关键：**证书 90 天到期，acme.sh 自动续期后会执行这条命令让 nginx 重载**，你不用管。不配的话续期后仍是旧证书。

> 此刻 frontend 还跑着纯 HTTP 配置，这条 reload 不会报错但也没用到证书——证书真正生效在下一步 `up -d`。
> 之后的自动续期就正常了。

## 6. 启用 HTTPS

**⓪ 先放行安全组**（漏了这步，后面所有验证都会超时）

百度云控制台 → 安全组 → 入站规则，加两条：`TCP:80` 和 `TCP:443`，源 `0.0.0.0/0`。
确认系统防火墙也放行了：

```bash
sudo ufw status          # 如果 active，需要 ufw allow 80,443/tcp
```

**① 改 nginx 配置里的域名**

`frontend/nginx.https.conf` 里有两处 `server_name rules.example.com;`，都换成你的真实域名。

**② 改 .env 的端口**

```env
HTTP_PORT=80
HTTPS_PORT=443
```

不改成 80 的话，宿主机只会映射 8080，`http://域名` 打不开（虽然 `https://` 能通）。

**③ 启动**

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.4g.yml \
  -f docker-compose.https.yml \
  up -d
```

三个 `-f` 缺一不可：第二个管内存，第三个管 443 和证书。

**④ 验证**

```bash
curl -I https://rules.xxx.top                 # 200
curl -I http://rules.xxx.top                  # 301 → https
echo | openssl s_client -connect rules.xxx.top:443 -servername rules.xxx.top 2>/dev/null | grep "Verify return code"
# 期望：Verify return code: 0 (ok)

docker compose logs --tail=30 frontend        # 没有 "cannot load certificate" 之类报错
```

浏览器打开 `https://rules.xxx.top`，地址栏应该有锁。

## 7. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `nginx: [emerg] cannot load certificate` | `./certs/` 里没文件，或文件名不对 | 必须是 `fullchain.pem` + `privkey.pem` |
| 浏览器「您的连接不是私密连接」 | 证书链不完整 | 确认用的是 `fullchain.pem` 而不是 `cert.pem` |
| `https://` 能开但 `/api/` 502 | nginx 在 appnet 网络里找不到 backend | `docker compose ps` 看 backend 是否 healthy |
| 域名打不开、IP:8080 正常 | 没备案，80/443 被拦 | 等备案通过，或改走方案 B |
| 申请证书报 `Timeout during connect` | 80 端口不通 | 云控制台安全组放行 80/443；`ufw status` 确认没拦 |
| 证书 90 天后过期 | `--reloadcmd` 没配或失败 | `acme.sh --list` 看下次续期时间；手动跑一次 `acme.sh --renew -d 域名 --force` |

**安全组**：百度云控制台 → 安全组 → 入站规则，放行 `80`、`443`（`8080` 可以关掉了，全部走 443）。

## 8. 方案 B：Cloudflare 免备案（赶时间用）

不推荐长期用，但 30 分钟能通。

1. 域名 NS 改到 Cloudflare（免费计划）；
2. DNS 加 A 记录指向服务器 IP，**代理状态开橙色云**；
3. SSL/TLS → 模式选 **Flexible**（源站只有 HTTP，不配证书）；
4. 源站保持现状（只开 8080），**不要**开 80/443——不开就不会触发备案拦截。

代价：Cloudflare 免费版回源走海外节点，国内访问延迟 200–500ms，3M 带宽下首屏比直连慢一倍；且 CF→源站是明文。

**答辩演示不要用这个方案**，延迟不稳定会影响体感。

## 9. 启用后要做的事

**① 关掉自助注册（防 API 被刷）**

`.env` 里改：

```env
ALLOW_REGISTRATION=0
RATE_LIMIT_PER_DAY=100
RATE_LIMIT_PER_MINUTE=5
```

改完 `docker compose up -d backend` 重启生效。登录页仍支持一键填 `admin` / `student` 演示账号。

**② 确认 swap 已写进 fstab**

```bash
grep swap /etc/fstab        # 必须有 /swapfile 那一行
free -h                     # Swap 一列要显示 2.0Gi
```

漏了这步，服务器重启后 OOM，面试官点开正好 502。

**③ 加监控（可选但便宜）**

```bash
# 每天早上看一眼磁盘和内存，40G 盘容易被 Docker 构建缓存吃满
docker system df
df -h /
```

## 10. 简历里怎么写

```text
✅ 校园服务智能问答系统 | 独立开发 | 在线演示：https://rules.xxx.top
❌ 校园服务智能问答系统 | 在线演示：http://123.45.67.89:8080
```

**域名换了服务器只改 DNS，简历不用动**；IP 一变简历就废。这是选域名的核心理由，不只是好看。

注意 ¥39 是首年价，续费 ¥2265。校招周期结束前别释放实例，一年后到期再说。
