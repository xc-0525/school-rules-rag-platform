# 安全自查与复习资料（分支 `feat/security-audit`）

本目录是**独立分支**上的安全工作区，不进 main；main 保持可回滚基线。

## 目录

| 文件 | 内容 | 什么时候用 |
|------|------|-----------|
| `01-web-vuln-checklist.md` | 20 类 Web 漏洞 × 测试要点/Payload/判定/修复 + 本仓库逐项关联 | 自查系统时；笔试前刷"测试要点"列 |
| `02-api-testcases.md` | 48 项自动化用例台账 + 10 项手工验证项，含运行命令与结果 | 跑测试、发版前过手工清单 |
| `03-exam-cram.md` | 绿盟安全工程师笔试速记：OWASP/CWE/CVSS/密码学/端口/CVE/流程/高频简答/易错陷阱 | 考前 1–2 小时 |

## 30 秒上手

```bash
cd backend
G:/DevEnv/Python/Python314/python.exe -m unittest discover -s tests.security -t . -v
```

> 注意：根目录 `.venv` 缺 sqlalchemy，是**不完整环境**；跑后端测试用系统 Python。

## 本系统攻击面地图（结论摘要）

| 入口 | 信任边界 | 已有防护 | 残余风险 |
|------|----------|----------|----------|
| `POST /auth/login` `register` | 公网 | PBKDF2 慢哈希 + 统一失败文案 + 注册白名单/保留用户名 + IP 分钟限流 | 无失败锁定与验证码（P1） |
| `Authorization: Bearer` | 公网→会话 | HMAC 签名、先验签后解析、exp 校验、jti 吊销 | token 存 localStorage（P1）、无 aud/iss、无刷新令牌 |
| `POST /documents/upload*` | 仅 admin | 后缀白名单、分类型大小上限、UUID 重命名、先鉴权后读 body | 未校验文件头（P3） |
| `GET /documents/{id}/preview` | 登录用户 | `resolve()` + `is_relative_to` 双重目录校验 + nosniff | — |
| `GET /conversations*` | 登录用户，对象级 | 归属校验下推到查询层（IDOR→404） | — |
| `POST /chat`（含 LLM） | 登录用户 | 三层限流 + 防幻觉拒答 | 提示词注入需持续测试（SEC-M-10） |
| `/health` `/docs` | 公网 | — | 部署信息/接口清单暴露（P2，生产收敛） |
| CORS | 浏览器 | 固定 localhost 白名单，非通配 | 生产应由反代同源托管，勿改 `*` |

## 已验证的结论（截至 2026-11-07）

- 自动化：`tests/security/` **48 项全部通过**（mock 模式，自包含）。
- 静态：无 `eval/exec/命令执行/反序列化`、无 f-string SQL、无服务端外发请求（SSRF 面 = 0）。
- 手工待办：见 `02-api-testcases.md` 第 3 节（CORS 反射、安全头、TLS、爆破、.git 泄露等）。

## 分支约定

- 本分支只新增：`docs/security/**` 与 `backend/tests/security/**`，不改既有代码。
- 回归底线：安全用例必须始终可跑、全绿；发现防护行被误删时先修代码再谈。
- 测试规范遵循用户级技能 `boundary-and-multiturn-testing`（边界/中间态/多轮/上下文切换/域外五类）。

## 免责与边界

所有测试仅针对**本项目自有代码与自建环境**执行（TestClient / mock / 本机部署），不包含对任何第三方系统的探测；清单中的 Payload 仅用于理解原理与自查复现。
