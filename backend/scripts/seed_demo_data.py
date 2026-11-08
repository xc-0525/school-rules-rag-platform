"""答辩演示数据准备（幂等，可反复执行）。

为什么需要：现场打开校园服务时，报修全是"已完成/已关闭"（流转按钮点不了）、
失物没有"待审核"（审核按钮点不了）、预约全是过期或已取消（取消按钮点不了），
评委看不到业务闭环，只能看静态列表。

本脚本只补演示所需的**在途状态**数据，并清掉历史测试脏数据。
不新增任何接口、不改代码，纯数据操作。

用法：
    cd backend
    python scripts/seed_demo_data.py            # 补齐 + 清理
    python scripts/seed_demo_data.py --dry-run  # 只看会做什么
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402

DEMO_USER_ID = 4  # demo 账号
STUDENT_USER_ID = 2

# 需要清理的测试脏数据（用户名/描述特征）
DIRTY_USERNAMES = ("1234__", "xc123", "uitest_admin", "uitest_stu", "uicheck_stu")
DIRTY_REPAIR_TITLES = ("123",)


def connect(settings: Settings):
    import pymysql
    from sqlalchemy.engine import make_url

    url = make_url(settings.mysql_url)
    return pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        database=url.database,
        charset="utf8mb4",
        autocommit=True,
    )


def cleanup(cursor, dry_run: bool) -> None:
    placeholders = ",".join(["%s"] * len(DIRTY_USERNAMES))
    if dry_run:
        cursor.execute(f"SELECT COUNT(*) FROM users WHERE username IN ({placeholders})", DIRTY_USERNAMES)
        print(f"  [dry] 将删除测试账号 {cursor.fetchone()[0]} 个: {', '.join(DIRTY_USERNAMES)}")
    else:
        cursor.execute(f"DELETE FROM users WHERE username IN ({placeholders})", DIRTY_USERNAMES)
        print(f"  清理测试账号 {cursor.rowcount} 个")

    for title in DIRTY_REPAIR_TITLES:
        if dry_run:
            cursor.execute("SELECT COUNT(*) FROM repair_requests WHERE title=%s", (title,))
            print(f"  [dry] 将删除占位报修 {cursor.fetchone()[0]} 条（title={title}）")
        else:
            cursor.execute("DELETE FROM repair_requests WHERE title=%s", (title,))
            print(f"  清理占位报修 {cursor.rowcount} 条")


def seed_repairs(cursor, dry_run: bool) -> None:
    """补 1 条待受理 + 1 条处理中，让管理员能演示受理与完成。"""
    wanted = [
        {
            "title": "阳台灯不亮",
            "description": "阳台吸顶灯开关无反应，夜间照明受影响，希望尽快维修。",
            "category": "water_power",
            "building": "12栋",
            "room": "308",
            "contact": "138****0001",
            "status": "pending",
            "handler_id": None,
            "handler_remark": None,
        },
        {
            "title": "网络接口松动",
            "description": "书桌下方网口接触不良，插上网线偶尔断网，已确认网线正常。",
            "category": "network",
            "building": "12栋",
            "room": "308",
            "contact": "138****0001",
            "status": "processing",
            "handler_id": 1,
            "handler_remark": "已联系网络中心，约定今日下午上门检查",
        },
    ]
    for item in wanted:
        cursor.execute(
            "SELECT id FROM repair_requests WHERE title=%s AND building=%s AND room=%s",
            (item["title"], item["building"], item["room"]),
        )
        if cursor.fetchone():
            print(f"  已存在报修：{item['title']}（{item['status']}）")
            continue
        if dry_run:
            print(f"  [dry] 将新增报修：{item['title']}（{item['status']}）")
            continue
        cursor.execute(
            "INSERT INTO repair_requests "
            "(user_id,title,description,category,building,room,contact,status,"
            "handler_id,handler_remark,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())",
            (
                DEMO_USER_ID, item["title"], item["description"], item["category"],
                item["building"], item["room"], item["contact"], item["status"],
                item["handler_id"], item["handler_remark"],
            ),
        )
        print(f"  新增报修：{item['title']}（{item['status']}）")


def seed_lost_found(cursor, dry_run: bool) -> None:
    """补 2 条待审核（管理员可演示通过/驳回）+ 2 条已公示（公示栏不空）。"""
    wanted = [
        {
            "kind": "lost", "title": "丢失校园卡（张同学）",
            "description": "在图书馆三楼自习区遗失校园卡一张，卡面姓名张同学，拾到请联系。",
            "location": "图书馆三楼自习区", "status": "pending_review",
        },
        {
            "kind": "found", "title": "捡到一串钥匙",
            "description": "在体育馆门口捡到钥匙一串，共 3 把，带蓝色钥匙扣。",
            "location": "体育馆门口", "status": "pending_review",
        },
        {
            "kind": "found", "title": "捡到蓝牙耳机",
            "description": "教学楼 A 座 201 教室捡到白色蓝牙耳机一副，已交至值班室。",
            "location": "教学楼 A 座 201", "status": "published",
        },
        {
            "kind": "lost", "title": "丢失雨伞（黑色长柄）",
            "description": "食堂二楼用餐后遗落黑色长柄雨伞，伞柄有姓名贴。",
            "location": "食堂二楼", "status": "published",
        },
    ]
    for item in wanted:
        cursor.execute(
            "SELECT id FROM lost_found_items WHERE title=%s AND location=%s",
            (item["title"], item["location"]),
        )
        if cursor.fetchone():
            print(f"  已存在失物：{item['title']}（{item['status']}）")
            continue
        if dry_run:
            print(f"  [dry] 将新增失物：{item['title']}（{item['status']}）")
            continue
        reviewed = item["status"] != "pending_review"
        cursor.execute(
            "INSERT INTO lost_found_items "
            "(user_id,kind,title,description,location,contact,image_urls,status,"
            "reviewer_id,review_remark,reviewed_at,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())",
            (
                STUDENT_USER_ID, item["kind"], item["title"], item["description"],
                item["location"], "139****0002", "[]", item["status"],
                1 if reviewed else None,
                "信息完整，予以公示" if reviewed else None,
                datetime.now() if reviewed else None,
            ),
        )
        print(f"  新增失物：{item['title']}（{item['status']}）")


def seed_reservations(cursor, dry_run: bool) -> None:
    """补 2 条未来日期的有效预约：学生能演示"取消"，也不占满容量。"""
    wanted = [
        {"location_id": 1, "slot": "morning", "day_offset": 1},
        {"location_id": 3, "slot": "evening", "day_offset": 2},
    ]
    for item in wanted:
        res_date = (date.today() + timedelta(days=item["day_offset"])).isoformat()
        cursor.execute(
            "SELECT id FROM location_reservations "
            "WHERE user_id=%s AND location_id=%s AND res_date=%s AND slot=%s AND status='reserved'",
            (DEMO_USER_ID, item["location_id"], res_date, item["slot"]),
        )
        if cursor.fetchone():
            print(f"  已存在预约：{res_date} {item['slot']}")
            continue
        if dry_run:
            print(f"  [dry] 将新增预约：location={item['location_id']} {res_date} {item['slot']}")
            continue
        cursor.execute(
            "INSERT INTO location_reservations "
            "(location_id,user_id,res_date,slot,status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,'reserved',NOW(),NOW())",
            (item["location_id"], DEMO_USER_ID, res_date, item["slot"]),
        )
        print(f"  新增预约：location={item['location_id']} {res_date} {item['slot']}")


def summary(cursor) -> None:
    print("\n当前演示数据：")
    for table, column in (
        ("repair_requests", "status"),
        ("lost_found_items", "status"),
        ("location_reservations", "status"),
    ):
        cursor.execute(f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}")
        dist = "、".join(f"{name} {count}" for name, count in cursor.fetchall())
        print(f"  {table}: {dist or '（空）'}")
    cursor.execute("SELECT COUNT(*) FROM users")
    print(f"  users: {cursor.fetchone()[0]} 个")


def main() -> int:
    parser = argparse.ArgumentParser(description="答辩演示数据准备（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的操作")
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.mysql_url:
        print("错误：未配置 MYSQL_URL，无法准备演示数据")
        return 1

    connection = connect(settings)
    with connection.cursor() as cursor:
        print("1) 清理历史测试脏数据")
        cleanup(cursor, args.dry_run)
        print("2) 补齐在途报修（待受理 / 处理中）")
        seed_repairs(cursor, args.dry_run)
        print("3) 补齐失物（待审核 / 已公示）")
        seed_lost_found(cursor, args.dry_run)
        print("4) 补齐未来预约（可演示取消）")
        seed_reservations(cursor, args.dry_run)
        if not args.dry_run:
            summary(cursor)
    connection.close()
    print("\n完成。演示前请刷新页面；如需重建，可重复执行本脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
