"""扩展功能迁移（N-01/N-20/N-25）：按列/表存在性判断后执行，可重复运行。

用法（backend 目录）：
    python scripts/apply_extension_migration.py            # 应用全部
    python scripts/apply_extension_migration.py --dry-run  # 只打印将执行的语句

设计：
- 不按文件顺序整段执行，而是拆成"目标 + 判断 + DDL"三元组，缺什么补什么；
- 共享库上可安全重复跑（另一分支的实例不受影响，新列均为 NULL/有默认值）；
- 只加不删，不回滚已有数据。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db.session import new_session  # noqa: E402


def column_exists(session, table: str, column: str) -> bool:
    row = session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return bool(row)


def index_exists(session, table: str, index: str) -> bool:
    row = session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
        ),
        {"t": table, "i": index},
    ).scalar()
    return bool(row)


def table_exists(session, table: str) -> bool:
    row = session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
        ),
        {"t": table},
    ).scalar()
    return bool(row)


# (说明, 表, 列或索引名, 类型, DDL)
COLUMNS: list[tuple[str, str, str, str]] = [
    ("N-01 工单处理人", "tickets", "handler_id", "column",
     "ALTER TABLE tickets ADD COLUMN handler_id INT NULL DEFAULT NULL COMMENT '处理人（管理员用户 id）'"),
    ("N-01 工单处理意见", "tickets", "handler_remark", "column",
     "ALTER TABLE tickets ADD COLUMN handler_remark TEXT NULL COMMENT '处理意见（关闭必填）'"),
    ("N-01 工单关闭时间", "tickets", "closed_at", "column",
     "ALTER TABLE tickets ADD COLUMN closed_at DATETIME NULL DEFAULT NULL COMMENT '关闭时间'"),
    ("N-20 学历层次", "users", "education_level", "column",
     "ALTER TABLE users ADD COLUMN education_level VARCHAR(16) NOT NULL DEFAULT 'unset' "
     "COMMENT '本科/专科/unset'"),
    ("N-20 学院", "users", "college", "column",
     "ALTER TABLE users ADD COLUMN college VARCHAR(64) NULL DEFAULT NULL"),
    ("N-20 年级", "users", "grade", "column",
     "ALTER TABLE users ADD COLUMN grade VARCHAR(16) NULL DEFAULT NULL"),
    ("N-20 文档适用对象", "documents", "education_level", "column",
     "ALTER TABLE documents ADD COLUMN education_level VARCHAR(16) NOT NULL DEFAULT 'general' "
     "COMMENT '本科/专科/general'"),
    ("N-25 反馈归因", "feedback", "attribution", "column",
     "ALTER TABLE feedback ADD COLUMN attribution VARCHAR(32) NULL DEFAULT NULL "
     "COMMENT '语料缺失/答案错误/引用错误/问题不清'"),
    ("N-25 反馈处理状态", "feedback", "handle_status", "column",
     "ALTER TABLE feedback ADD COLUMN handle_status VARCHAR(16) NOT NULL DEFAULT 'pending'"),
    ("N-25 反馈处理人", "feedback", "handler_id", "column",
     "ALTER TABLE feedback ADD COLUMN handler_id INT NULL DEFAULT NULL"),
    ("N-25 反馈处理时间", "feedback", "handled_at", "column",
     "ALTER TABLE feedback ADD COLUMN handled_at DATETIME NULL DEFAULT NULL"),
    ("N-25 反馈处理备注", "feedback", "handle_remark", "column",
     "ALTER TABLE feedback ADD COLUMN handle_remark VARCHAR(500) NULL DEFAULT NULL"),
]

INDEXES: list[tuple[str, str, str]] = [
    ("N-01 工单状态索引", "tickets", "idx_tickets_status_created"),
    ("N-25 反馈状态索引", "feedback", "idx_feedback_status"),
]

INDEX_DDL: dict[str, str] = {
    "idx_tickets_status_created": "ALTER TABLE tickets ADD INDEX idx_tickets_status_created (status, created_at)",
    "idx_feedback_status": "ALTER TABLE feedback ADD INDEX idx_feedback_status (handle_status, created_at)",
}

TABLES: list[tuple[str, str, Iterable[str]]] = [
    (
        "N-25 未命中标记",
        "unmatched_marks",
        [
            "CREATE TABLE IF NOT EXISTS unmatched_marks (",
            "  query_hash   CHAR(40)     NOT NULL COMMENT '归一化问题的 sha1',",
            "  query_text   VARCHAR(500) NOT NULL COMMENT '代表性原文',",
            "  status       VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending/ignored/resolved',",
            "  remark       VARCHAR(500) NULL DEFAULT NULL,",
            "  handler_id   INT          NULL DEFAULT NULL,",
            "  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,",
            "  updated_at   DATETIME     NULL DEFAULT NULL,",
            "  PRIMARY KEY (query_hash)",
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='未命中问题处理标记'",
        ],
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.mysql_url:
        print("未配置 MYSQL_URL，跳过迁移（内存模式无需建表）")
        return 0

    applied = 0
    with new_session(settings.mysql_url) as session:
        for label, table, name, kind, ddl in COLUMNS:
            if kind != "column":
                continue
            if not table_exists(session, table):
                print(f"跳过 {label}：表 {table} 不存在")
                continue
            if column_exists(session, table, name):
                continue
            print(f"+ {label}：{table}.{name}")
            if not args.dry_run:
                session.execute(text(ddl))
            applied += 1

        for label, table, index in INDEXES:
            if not table_exists(session, table) or index_exists(session, table, index):
                continue
            print(f"+ {label}：{table}.{index}")
            if not args.dry_run:
                session.execute(text(INDEX_DDL[index]))
            applied += 1

        for label, table, lines in TABLES:
            if table_exists(session, table):
                continue
            print(f"+ {label}：{table}")
            if not args.dry_run:
                session.execute(text("\n".join(lines)))
            applied += 1

        if not args.dry_run:
            session.commit()

    print(f"完成：{applied} 项变更{'（dry-run 未执行）' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
