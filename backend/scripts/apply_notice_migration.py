#!/usr/bin/env python
"""应用公告模块的字段迁移（幂等）。

MySQL 5.5 不支持 `ADD COLUMN IF NOT EXISTS`，直接跑 .sql 重复执行会报错。
这里先查 information_schema，缺哪列补哪列，可以反复执行。

用法：
    python scripts/apply_notice_migration.py            # 读 .env 的 MYSQL_URL
    python scripts/apply_notice_migration.py --url mysql+pymysql://root:@127.0.0.1:3306/school_rules_rag
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import Settings  # noqa: E402

COLUMNS: list[tuple[str, str]] = [
    ("category", "ALTER TABLE announcements ADD COLUMN category VARCHAR(32) NOT NULL DEFAULT 'other' COMMENT '分类：water/power/network/teaching/life/other'"),
    ("effective_at", "ALTER TABLE announcements ADD COLUMN effective_at DATETIME NULL DEFAULT NULL COMMENT '生效时间'"),
    ("expires_at", "ALTER TABLE announcements ADD COLUMN expires_at DATETIME NULL DEFAULT NULL COMMENT '失效时间，NULL=长期有效'"),
    ("priority", "ALTER TABLE announcements ADD COLUMN priority VARCHAR(16) NOT NULL DEFAULT 'normal' COMMENT 'normal=普通；urgent=紧急（登录后顶部横幅）'"),
]

INDEXES: list[tuple[str, str]] = [
    ("idx_ann_time", "ALTER TABLE announcements ADD KEY idx_ann_time (expires_at, effective_at)"),
    (
        "idx_ann_banner",
        "ALTER TABLE announcements ADD KEY idx_ann_banner (priority, is_pinned, status, expires_at)",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="公告模块字段迁移（幂等）")
    parser.add_argument("--url", default="", help="MySQL URL，默认取 .env")
    args = parser.parse_args()

    raw_url = args.url or Settings.from_env().mysql_url
    if not raw_url:
        print("错误：未配置 MYSQL_URL，也没有传 --url")
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
    database = url.database
    changed = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='announcements'",
                (database,),
            )
            existing = {row[0] for row in cursor.fetchall()}

            for name, ddl in COLUMNS:
                if name in existing:
                    print(f"  跳过 {name}（已存在）")
                    continue
                cursor.execute(ddl)
                print(f"  新增 {name}")
                changed += 1

            for name, ddl in INDEXES:
                cursor.execute(
                    "SELECT INDEX_NAME FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='announcements' AND INDEX_NAME=%s",
                    (database, name),
                )
                if cursor.fetchone() is not None:
                    print(f"  跳过索引 {name}（已存在）")
                    continue
                cursor.execute(ddl)
                print(f"  新增索引 {name}")
                changed += 1

            # 历史数据回填生效时间
            cursor.execute(
                "UPDATE announcements SET effective_at = created_at WHERE effective_at IS NULL"
            )
            if cursor.rowcount:
                print(f"  回填 {cursor.rowcount} 条历史公告的生效时间")
                changed += 1

            # 存量公告一律保持普通级别，避免升级后突然弹横幅
            cursor.execute(
                "UPDATE announcements SET priority = 'normal' "
                "WHERE priority IS NULL OR priority = ''"
            )
            if cursor.rowcount:
                print(f"  回填 {cursor.rowcount} 条历史公告的优先级")
                changed += 1
        connection.commit()
    finally:
        connection.close()

    print(f"迁移完成，变更 {changed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
