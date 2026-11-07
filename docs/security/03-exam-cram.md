# 绿盟安全工程师笔试·速记手册

> 定位：考前 1–2 小时能刷完的骨架 + 考场可回忆的细节锚点。
> 深度版对应 `01-web-vuln-checklist.md`（原理/测试/修复三段式），本文只留"必答句"。

## 1. 一页总表：漏洞 → 一句话

| 漏洞 | 原理一句话 | 测试一句话 | 修复一句话 |
|------|-----------|-----------|-----------|
| SQL 注入 | 数据被拼进 SQL 改变语义 | `1=1/1=2` 对照 + `sleep` 延时 | 预编译，排序/表名走白名单 |
| XSS | 可控内容进 HTML 上下文被执行 | 上下文构造 payload 看是否被编码 | 按上下文输出编码 + CSP + HttpOnly |
| CSRF | 浏览器自动带 Cookie 代发请求 | 删 token/Referer 再发一遍 | CSRF Token + SameSite + Origin 校验 |
| 越权 | 服务端没校验"主体↔客体"归属 | 改 ID、改角色、删凭据三连 | 每请求服务端校验归属，查询带 user_id |
| 认证缺陷 | 弱口令/爆破/枚举/固定 | 看失败响应是否一致、有无锁定 | 慢哈希+锁定+验证码+统一文案 |
| 会话/JWT | 凭据可伪造、可复用、不过期 | 改 payload、alg=none、弱密钥爆破 | 严格验签+校验 exp/aud+吊销机制 |
| 文件上传 | 服务端把恶意文件存成了可执行/可解析 | 后缀、Content-Type、魔术字节三路绕 | 白名单+改名+存储隔离+头校验 |
| 路径穿越 | `../` 逃出允许目录 | 编码变形 + 绝对路径试探 | realpath 后前缀校验，ID 映射代替路径 |
| SSRF | 服务端替你访问任意 URL | 内网地址/云元数据/协议变形 | 解析后校验 IP + 协议白名单 + 禁跳转 |
| XXE | XML 外部实体读文件/打内网 | DOCTYPE + ENTITY 引用 | 禁 DTD 与外部实体 |
| 反序列化 | 还原对象触发恶意链 | 特征串 + gadget 链 | 不反序列化不可信数据/白名单 |
| 信息泄露 | 报错/备份/文档/版本外露 | 500 回栈、.git、/docs | 统一异常、清理部署产物 |
| 配置错误 | 默认值/调试态/通配 CORS | 逐项对照安全基线 | 最小化 + 基线核查 |
| 业务逻辑 | 流程/数值/并发假设被打破 | 并发提交、负数、跳步骤 | 服务端状态机+幂等+限额 |

## 2. 必背清单

### 2.1 OWASP Top 10（2021）——按顺序背

| # | 英文 | 中文 | 一句话 |
|---|------|------|--------|
| A01 | Broken Access Control | 失效的访问控制 | 越权/IDOR/未授权 |
| A02 | Cryptographic Failures | 加密机制失效 | 明文/弱哈希/弱随机 |
| A03 | Injection | 注入 | SQL/XSS/XXE/模板（XSS 归入 A03） |
| A04 | Insecure Design | 不安全设计 | 缺威胁建模、逻辑缺陷 |
| A05 | Security Misconfiguration | 安全配置错误 | 默认配置/调试态/Verbose 报错 |
| A06 | Vulnerable and Outdated Components | 易受攻击的过时组件 | 组件 CVE |
| A07 | Identification and Authentication Failures | 身份识别与认证失败 | 爆破/弱口令/会话缺陷 |
| A08 | Software and Data Integrity Failures | 软件与数据完整性失效 | 反序列化/供应链/CI 篡改 |
| A09 | Security Logging and Monitoring Failures | 日志与监控失效 | 出事不知道 |
| A10 | SSRF | 服务端请求伪造 | 服务端替攻击者发请求 |

对比记忆：2017 的 XML External Entities（XXE）并入 2021 的 A03；Insecure Deserialization 并入 A08；2021 新增 A04 不安全设计与 A10 SSRF。

### 2.2 OWASP API Security Top 10（2023）

API1 BOLA（对象级越权）｜API2 认证失效｜API3 对象属性级越权｜API4 无限制资源消耗｜API5 功能级越权｜API6 敏感业务流无限制访问｜API7 SSRF｜API8 安全配置错误｜API9 资产清单管理不当｜API10 不安全调用第三方 API。

### 2.3 常考 CWE 编号

| CWE | 名称 | 对应 |
|-----|------|------|
| CWE-79 | 跨站脚本 XSS | 反射/存储/DOM |
| CWE-89 | SQL 注入 | 拼接 SQL |
| CWE-22 | 路径穿越 | `../` |
| CWE-78 | OS 命令注入 | system/exec |
| CWE-94 | 代码注入 | eval/动态执行 |
| CWE-352 | CSRF | 跨站请求伪造 |
| CWE-434 | 危险文件上传 | 无限制上传 |
| CWE-611 | XXE | 外部实体 |
| CWE-502 | 反序列化不可信数据 | gadget 链 |
| CWE-918 | SSRF | 服务端请求伪造 |
| CWE-287 | 认证不当 | 逻辑缺陷 |
| CWE-306 | 关键功能缺少认证 | 未授权访问 |
| CWE-798 | 硬编码凭据 | 写死的密钥/口令 |
| CWE-200 | 信息暴露 | 报错/响应冗余 |
| CWE-732 | 权限分配不当 | 文件/目录权限过宽 |

### 2.4 CVSS 3.1

- 基础指标：**AV** 攻击向量(N/A/L/P) · **AC** 攻击复杂度(L/H) · **PR** 所需权限(N/L/H) · **UI** 用户交互(N/R) · **S** 影响范围(U/C) · **C/I/A** 机密性/完整性/可用性(N/L/H)。
- 等级：None 0｜Low 0.1–3.9｜Medium 4.0–6.9｜High 7.0–8.9｜Critical 9.0–10.0。
- 读向量示例 `AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H` = 远程、无交互、无权限、影响机密+完整+可用 → 9.8 Critical。
- `S:Changed`（越界影响，如虚拟机逃逸）会抬分。

### 2.5 安全响应头与 Cookie

| 头 | 取值与作用 |
|----|-----------|
| Content-Security-Policy | `default-src 'self'` 限制资源来源，挡 XSS 加载外链 |
| X-Frame-Options | `DENY` / `SAMEORIGIN` 防点击劫持 |
| X-Content-Type-Options | `nosniff` 禁 MIME 嗅探 |
| Strict-Transport-Security | `max-age=31536000; includeSubDomains` 强制 HTTPS |
| Referrer-Policy | `strict-origin-when-cross-origin` 等 |
| Permissions-Policy | 禁 geolocation/camera 等 |

Set-Cookie 六件套：`Secure`（仅 HTTPS）、`HttpOnly`（JS 不可读）、`SameSite=Lax/Strict/None`（CSRF 缓解）、`Path`、`Domain`（越窄越好）、`Expires/Max-Age`。

### 2.6 同源与 CORS

- 同源 = **协议 + 域名 + 端口**全同。
- 跨域写（表单/跳转）不受限；跨域**读取**受同源限制。
- 简单请求直接发；非简单请求（PUT/DELETE、自定义头、`Content-Type: application/json`）先发 **OPTIONS 预检**，带 `Access-Control-Request-Method/Headers`。
- 服务端返回 `Access-Control-Allow-Origin` 才允许读响应；`Allow-Credentials: true` 时 Origin 不能是 `*`。
- 细节：CORS 拦的是**响应读取**，非简单请求本身仍可能已到达服务端产生副作用。

### 2.7 密码学速记

- 三种"变换"分清：**编码**（Base64/URL，可逆无密钥）、**哈希**（MD5/SHA，单向）、**加密**（AES/RSA，可逆有密钥）。
- 对称：AES(128/192/256)、SM4(128) 快；**非对称**：RSA(≥2048)、ECC(256)、SM2 慢但解决密钥分发；混合加密 = 非对称送对称密钥。
- 哈希：MD5(128)/SHA-1(160) 已破禁用；SHA-256；SM3(256)；口令存储专用**慢哈希** PBKDF2/bcrypt/scrypt/Argon2 + 盐。
- MD5 不可逆，"MD5 解密网站"只是彩虹表查询。
- 签名 = 哈希 + 私钥（完整性+来源+不可否认）；MAC/HMAC = 哈希 + 共享密钥。
- 数字证书 = 公钥 + 身份 + CA 签名；校验链：签名 → 域名 SAN → 有效期 → 吊销（CRL/OCSP）。
- TLS：1.2 支持 RSA 静态交换；**1.3 只留 (EC)DHE + AEAD**，禁 RSA 静态交换与 CBC，支持 0-RTT。

### 2.8 端口速记

21 FTP｜22 SSH｜23 Telnet｜25 SMTP｜53 DNS｜80/443 HTTP(S)｜110 POP3｜143 IMAP｜135/139/445 RPC/NetBIOS/SMB｜1433 MSSQL｜1521 Oracle｜3306 MySQL｜3389 RDP｜5432 PostgreSQL｜6379 Redis｜8080/8443 Web-Alt｜9200 Elasticsearch｜11211 Memcached｜27017 MongoDB｜161 SNMP｜5900 VNC。

### 2.9 经典 CVE 对号入座

| CVE | 名称 | 类型 |
|-----|------|------|
| CVE-2021-44228 | Log4Shell（Log4j2） | JNDI 注入 RCE |
| CVE-2016-4437 | Shiro-550 | rememberMe 默认密钥反序列化 |
| CVE-2017-5638 | Struts2 S2-045 | Content-Type OGNL RCE |
| CVE-2020-1938 | Ghostcat（Tomcat AJP 8009） | 文件读取/包含 |
| CVE-2019-0708 | BlueKeep（RDP） | 远程代码执行 |
| CVE-2017-0144 | EternalBlue（SMB, MS17-010） | 缓冲区溢出（WannaCry 载体） |
| CVE-2014-0160 | Heartbleed（OpenSSL） | 心跳越界读内存 |
| CVE-2021-26855 | ProxyLogon（Exchange） | SSRF→RCE |
| CVE-2022-22965 | Spring4Shell | 参数绑定 RCE |
| CVE-2020-14882 | WebLogic | 未授权 RCE |

### 2.10 流程类

- **渗透测试 PTES 七阶段**：前期交互 → 情报收集 → 威胁建模 → 漏洞分析 → 利用 → 后渗透 → 报告。
- **应急响应 PDCERF**：准备 → 检测 → **抑制**(Containment) → **根除**(Eradication) → 恢复 → 跟踪总结。
- **取证易失性顺序**：CPU/缓存 → 内存 → 网络连接 → 进程 → 临时文件 → 磁盘 → 备份（先易失后持久）。
- **等保 2.0**：五级定级；技术五块（物理环境/通信网络/区域边界/计算环境/管理中心）+ 管理五块（制度/机构/人员/建设/运维）；核心三性：**保密性、完整性、可用性**；日志留存 ≥6 个月。
- **风险公式**：风险 = 资产价值 × 威胁 × 脆弱性。

## 3. 高频简答（答题要点式）

1. **SQL 注入原理与防御？** 用户输入拼接进 SQL 改变语义。防御：预编译参数化（唯一正解）、白名单处理表名列名排序、最小权限、关闭详细报错、WAF 兜底。
2. **XSS 三型与防御？** 反射/存储/DOM。防御按输出上下文编码（HTML 实体/属性/JS/URL），富文本白名单过滤，CSP，HttpOnly。
3. **CSRF 与 XSS 区别？** CSRF 借用户身份"发请求"（攻击者不知内容），XSS 在站点上下文"执行代码"（可读数据）。CSRF 防：Token/SameSite/Origin；XSS 防：编码/CSP。XSS 存在时 CSRF Token 可被读取，故要同时防。
4. **SSRF 如何打内网？** 让服务端请求 `127.0.0.1/10.x/169.254.169.254`（云元数据）、`dict://`/`gopher://` 打 Redis；绕过靠十进制 IP、302、DNS Rebinding。
5. **上传绕过有哪些？** 后缀（大小写/双后缀/%00/::$DATA）、MIME 伪造、魔术字节图片马、解析漏洞（Apache 多后缀/IIS 分号）、竞态、`.htaccess`。
6. **水平 vs 垂直越权？** 水平=同角色跨对象（改 ID）；垂直=低角色做高权操作（改接口/参数）。修复都是服务端逐请求校验。
7. **会话固定？** 登录前后 session id 不变，攻击者先种值后等登录，登录后继承权限。修：登录后重置会话 ID。
8. **JWT 结构与攻击面？** `Header.Payload.Signature`（Base64URL，不加密）。攻击：alg=none、RS256→HS256 混淆、弱密钥爆破、kid 注入、不校验 exp。
9. **HTTPS 握手（1.2）？** ClientHello（套件+随机数）→ ServerHello+证书 → 密钥交换（ECDHE/RSA）→ 双方派生会话密钥 → Finished；1.3 只需 1-RTT 且强制 PFS。
10. **对称 vs 非对称？** 对称快、密钥分发难；非对称慢、解决分发但慢 → 实际混合使用。
11. **为什么 MD5 不能存口令？** 快哈希利于 GPU 穷举+无盐彩虹表；应使用加盐慢哈希（bcrypt/Argon2/PBKDF2）。
12. **什么是零日？** 无补丁、厂商未知；只能靠纵深防御+行为检测缓解。
13. **如何判断盲注？** 布尔：`1=1/1=2` 页面稳定差异；时间：`sleep(n)` 响应延迟且可复现（多次测量）。
14. **拿到 shell 后做什么？** 权限确认→信息收集（whoami/ipconfig/网络）→持久化评估→内网横向探测→按授权边界停止→写报告（后渗透阶段）。
15. **IDS vs IPS？** IDS 旁路检测告警；IPS 串联阻断。FW 管四层访问控制，WAF 管七层 HTTP 内容。
16. **WAF 常见绕过？** 编码（URL/双重/Unicode）、注释内联、大小写、等价函数、分块传输、HTTP 参数污染、超长参数截断。
17. **应急拿到受害主机先做什么？** 隔离网络（抑制）而不直接关机（保内存证据）→ 取证镜像 → 排查进程/计划任务/启动项/账号 → 修漏洞 → 恢复 → 复盘。
18. **验证码能防 CSRF 吗？** 能增加成本但非对症；对症是 Token/SameSite。验证码主要防爆破与自动化。
19. **CSP 是什么？** 响应头指令集限制页面能加载/执行哪些来源资源，`default-src 'self'; script-src 'self'`，挡多数 XSS 载荷执行。
20. **代码审计切入点？** 鉴权中间件覆盖面、SQL/命令拼接点、反序列化与文件操作、外部请求发起点、配置与密钥管理、错误处理与日志。

## 4. 易错陷阱（失分点）

1. Base64/Hex 是**编码**不是加密；HTTPS 只保传输不保应用安全。
2. 同源判定含**端口**；`http://1.2.3.4` 与 `http://domain→1.2.3.4` 不同源。
3. HttpOnly 防 XSS 偷 Cookie，但防不了 XSS 本身；token 放 localStorage 时 XSS 可直接偷 token。
4. "防住了"要用三要素举证：输入可控 + 穿越信任边界 + 可观测影响；扫描器结果必须人工复核。
5. `union select` 列数必须匹配且类型兼容；`order by` 注入不能参数化，要白名单。
6. 预编译防注入，但 `limit`/`order by`/表名不适用，仍是注入点。
7. CORS 未放行时浏览器拦响应，但非简单请求已到达服务端（副作用已发生）。
8. 301 永久重定向会被浏览器缓存，测试重定向漏洞时记得清缓存。
9. 漏洞≠风险；同一漏洞在内网与公网资产上评级不同（AV/PR/UI 变化）。
10. Shiro-550 默认密钥 `kPH+bIxk5D2deZiIxcaaaA==`（AES），改默认密钥即可大幅缓解——"默认值"是配置错误类高频考点。

## 5. 把本项目讲成亮点（面试话术）

- **口令存储**：PBKDF2-HMAC-SHA256、12 万次迭代、16 字节随机盐、`hmac.compare_digest` 定时安全比较——能讲清"为什么慢哈希+为什么常数时间比较"。
- **令牌**：自实现 HMAC-SHA256 签名单据，**先验签后解析 JSON**（避免对不可信数据解析），`exp` 校验 + `jti` 吊销实现登出即失效；能对比 JWT 的 alg=none 攻击讲自己为什么校验签名先行。
- **越权**：对象归属下推到查询层（`get_conversation(id, user_id)` 不匹配即 404），管理操作与鉴权开关解耦（曾修复 `AUTH_REQUIRED=0` 时写权限放大的 P0）。
- **上传**：后缀白名单 + 分类型大小限制 + UUID 重命名 + "先鉴权再读 body"（未授权请求不落盘）+ 超限即删半成品。
- **路径安全**：预览接口 `resolve()` + `is_relative_to(allowed_roots)` 双重校验，防御"数据库记录被篡改后的二次穿越"。
- **前端 XSS**：唯一 `v-html` 的 markdown 渲染器关闭 raw HTML、外链强制 `noopener noreferrer`；知道残余风险（localStorage 存 token）与改进方向。
- **限流**：分钟/日配额/并发三层闸门，且知道进程内计数在多 worker 下的放大问题（能讲 Redis 化方案）。
- **自查方法**：静态守卫测试锁关键防护行（防回归）+ 48 项安全用例覆盖边界/中间态/越权/配置矛盾——体现"验证而非口头保证"。
