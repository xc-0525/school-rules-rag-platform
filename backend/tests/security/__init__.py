"""安全自查测试包（分支 feat/security-audit）。

运行方式（在 backend 目录下）：

    python -m unittest discover -s tests.security -v
    # 或逐个文件
    python -m unittest tests.security.test_auth_session -v

设计约定：
- 全部用例自包含（mock 模式，不依赖 MySQL / Qdrant / Neo4j / 真实 LLM）；
- 每个用例对应 docs/security/02-api-testcases.md 中的一条编号用例；
- 发现回归时先修代码再改用例，不允许"改断言迁就实现"。
"""
