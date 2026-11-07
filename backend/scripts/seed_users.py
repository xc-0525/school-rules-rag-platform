"""P0-4：首启账号种子（幂等）。

背景：旧 init_mysql.sql 内嵌的固定口令哈希对应弱口令，且口令明文已随交付物
扩散（git 历史 + 交付归档），等效于口令公开。因此仓库不再内嵌任何哈希，
账号种子改由本脚本在容器首启执行：

- 口令来源：INIT_ADMIN_PASSWORD / INIT_STUDENT_PASSWORD；
  未配置则生成随机口令，仅在 stdout 打印一次；
- 显式拒绝已知弱口令与 <10 位口令；
- 幂等：账号已存在则跳过（不重置已有口令）；
- 失败不阻断容器启动（由 docker_entrypoint.py 捕获并告警）。

运行：python scripts/seed_users.py（通常由 docker_entrypoint 自动调用）
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.security import hash_password  # noqa: E402  纯标准库实现

# 已知弱口令黑名单（含本次下线的旧初始口令）
WEAK_PASSWORDS = {
    "admin123",
    "student123",
    "123456",
    "12345678",
    "123456789",
    "admin",
    "password",
    "admin123456",
    "qwerty",
    "111111",
    "888888",
}


def validate_password(password: str, label: str) -> str:
    """校验口令强度；弱口令/过短直接终止 seed（SystemExit 非 0）。"""
    pwd = (password or "").strip()
    if pwd.lower() in WEAK_PASSWORDS:
        raise SystemExit(f"{label} 为已知弱口令，拒绝 seed")
    if len(pwd) < 10:
        raise SystemExit(f"{label} 长度不足 10 位，拒绝 seed")
    return pwd


def _ensure_user(
    mysql_url: str, username: str, password: str, nickname: str, role: str
) -> bool:
    """幂等插入用户；账号已存在则跳过（不重置口令）。"""
    import pymysql
    from sqlalchemy.engine import make_url

    url = make_url(mysql_url)
    connection = pymysql.connect(
        host=url.host or "mysql",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        database=url.database,
        charset="utf8mb4",
        connect_timeout=5,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cursor.fetchone():
                return False
            cursor.execute(
                "INSERT INTO users (username, password, nickname, role, status, updated_at)"
                " VALUES (%s, %s, %s, %s, 1, NOW())",
                (username, hash_password(password), nickname, role),
            )
        connection.commit()
        return True
    finally:
        connection.close()


def main() -> None:
    admin_pwd = os.getenv("INIT_ADMIN_PASSWORD", "").strip() or secrets.token_urlsafe(16)
    validate_password(admin_pwd, "INIT_ADMIN_PASSWORD")
    student_pwd = os.getenv("INIT_STUDENT_PASSWORD", "").strip() or secrets.token_urlsafe(16)
    validate_password(student_pwd, "INIT_STUDENT_PASSWORD")

    mysql_url = os.getenv("MYSQL_URL", "").strip()
    if not mysql_url:
        print("[seed] 未配置 MYSQL_URL，跳过账号种子")
        return

    created_admin = _ensure_user(mysql_url, "admin", admin_pwd, "管理员", "admin")
    created_student = _ensure_user(mysql_url, "student", student_pwd, "演示学生", "student")

    if created_admin:
        # 口令只在 stdout 打印一次；容器日志会留存，首次登录后请立即修改
        print(f"[seed] admin 初始口令（仅打印一次，请立即登录修改）：{admin_pwd}")
    if created_student:
        print(f"[seed] student 初始口令（仅打印一次，请立即登录修改）：{student_pwd}")
    if not created_admin and not created_student:
        print("[seed] admin/student 账号已存在，跳过")


if __name__ == "__main__":
    main()
