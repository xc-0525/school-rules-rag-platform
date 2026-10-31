"""容器启动入口：等待 MySQL / Neo4j / Qdrant 就绪后执行实际命令。

Docker Compose 里 backend 和 ingest 共用同一个镜像：
- backend 默认命令是 uvicorn；
- ingest 通过 `docker compose run --rm ingest` 执行入库脚本。
"""

from __future__ import annotations

import os
import socket
import sys
import time
import urllib.request


def log(message: str) -> None:
    print(f"[docker-entrypoint] {message}", flush=True)


def wait_tcp(host: str, port: int, timeout: float = 180.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return
        except OSError:
            time.sleep(2)
    raise SystemExit(f"等待 {host}:{port} 超时（{timeout}s）")


def wait_mysql(timeout: float = 240.0) -> None:
    mysql_url = os.getenv("MYSQL_URL", "").strip()
    if not mysql_url:
        return
    import pymysql
    from sqlalchemy.engine import make_url

    url = make_url(mysql_url)
    host = url.host or "mysql"
    port = url.port or 3306
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            connection = pymysql.connect(
                host=host,
                port=port,
                user=url.username or "root",
                password=url.password or "",
                database=url.database,
                connect_timeout=3,
            )
            connection.close()
            log(f"MySQL 就绪：{host}:{port}/{url.database}")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    raise SystemExit(f"等待 MySQL 超时：{last_error}")


def wait_neo4j(timeout: float = 240.0) -> None:
    if os.getenv("GRAPH_MODE", "").strip().lower() != "neo4j":
        return
    from neo4j import GraphDatabase

    uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    user = os.getenv("NEO4J_USER", "").strip()
    password = os.getenv("NEO4J_PASSWORD", "").strip()
    auth = (user, password) if (user or password) else None
    deadline = time.time() + timeout
    last_error: Exception | None = None
    driver = GraphDatabase.driver(uri, auth=auth)
    try:
        while time.time() < deadline:
            try:
                driver.verify_connectivity()
                log(f"Neo4j 就绪：{uri}")
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(2)
    finally:
        driver.close()
    raise SystemExit(f"等待 Neo4j 超时：{last_error}")


def wait_qdrant(timeout: float = 240.0) -> None:
    if os.getenv("VECTOR_MODE", "").strip().lower() != "qdrant":
        return
    url = os.getenv("QDRANT_URL", "").strip()
    if not url:
        return
    ready_url = url.rstrip("/") + "/readyz"
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(ready_url, timeout=3) as response:
                if response.status < 500:
                    log(f"Qdrant 就绪：{url}")
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    raise SystemExit(f"等待 Qdrant 超时：{last_error}")


def main() -> None:
    wait_mysql()
    wait_neo4j()
    wait_qdrant()

    command = sys.argv[1:] or [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    log("执行：" + " ".join(command))
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
