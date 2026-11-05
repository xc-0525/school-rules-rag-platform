#!/usr/bin/env python
"""应用校园服务模块迁移（幂等）。

三张表全部 CREATE TABLE IF NOT EXISTS + 种子数据空表才插入，可反复执行。

用法：
    python scripts/apply_campus_service_migration.py            # 读 .env 的 MYSQL_URL
    python scripts/apply_campus_service_migration.py --url mysql+pymysql://root:@127.0.0.1:3306/school_rules_rag
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import Settings  # noqa: E402

MIGRATION_FILES = [
    Path(__file__).resolve().parents[2] / "sql" / "migrations" / "004_campus_services.sql",
    Path(__file__).resolve().parents[2] / "sql" / "migrations" / "005_location_naming.sql",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="校园服务模块建表迁移（幂等）")
    parser.add_argument("--url", default="", help="MySQL URL，默认取 .env")
    args = parser.parse_args()

    raw_url = args.url or Settings.from_env().mysql_url
    if not raw_url:
        print("错误：未配置 MYSQL_URL，也没有传 --url")
        return 1
    for migration in MIGRATION_FILES:
        if not migration.exists():
            print(f"错误：找不到迁移文件 {migration}")
            return 1

    import pymysql

    url = make_url(raw_url)
    connection = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        database=url.database,
        charset="utf8mb4",
    )
    statements: list[str] = []
    for migration in MIGRATION_FILES:
        for chunk in migration.read_text(encoding="utf-8").split(";"):
            # 去掉块内注释行，剩余内容才是真正的 SQL（否则注释前缀会让语句被整体跳过）
            lines = [ln for ln in chunk.splitlines() if not ln.strip().startswith("--")]
            sql = "\n".join(lines).strip()
            if sql:
                statements.append(sql)
    try:
        with connection.cursor() as cursor:
            for stmt in statements:
                cursor.execute(stmt)
        connection.commit()

        # 006：预约改为按容量占用，需要去掉 uk_slot 唯一键（存在性判断保证幂等）
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='location_reservations' AND INDEX_NAME=%s",
                (url.database, "uk_slot"),
            )
            if cursor.fetchone()[0]:
                cursor.execute("ALTER TABLE location_reservations DROP INDEX uk_slot")
                print("  移除唯一键 uk_slot（改为按容量占用）")
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='location_reservations' AND INDEX_NAME=%s",
                (url.database, "idx_res_slot"),
            )
            if not cursor.fetchone()[0]:
                cursor.execute(
                    "ALTER TABLE location_reservations "
                    "ADD KEY idx_res_slot (location_id, res_date, slot, status)"
                )
                print("  新增索引 idx_res_slot")
            # 006：研讨间按整间预约（容量 1），自习区/电子阅览仍按座位数
            cursor.execute(
                "UPDATE reservable_locations SET capacity = 1 "
                "WHERE zone = '研讨间' AND capacity <> 1"
            )
            if cursor.rowcount:
                print(f"  研讨间容量改为 1（{cursor.rowcount} 条）")
            # 007：取消原因列
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='location_reservations' AND COLUMN_NAME=%s",
                (url.database, "cancel_reason"),
            )
            if not cursor.fetchone()[0]:
                cursor.execute(
                    "ALTER TABLE location_reservations ADD COLUMN cancel_reason "
                    "VARCHAR(200) NULL DEFAULT NULL COMMENT '取消原因（管理员填写，学生可见）'"
                )
                print("  新增列 cancel_reason")
        connection.commit()

        ok = True
        with connection.cursor() as cursor:
            for table in ("repair_requests", "reservable_locations", "location_reservations", "lost_found_items"):
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (url.database, table),
                )
                exists = cursor.fetchone()[0]
                print(f"  {'就绪' if exists else '缺失'}: {table}")
                ok = ok and bool(exists)
        return 0 if ok else 1
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
