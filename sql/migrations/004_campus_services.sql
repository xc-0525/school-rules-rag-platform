-- 校园服务模块：报修 / 图书馆预约 / 失物招领
--
-- 三张独立表（不与 tickets 混用：状态机、限流、字段均不同，见 docs/campus-modules.md）。
-- MySQL 5.5 兼容：无 IF NOT EXISTS 的列级写法问题——本文件全部用
-- CREATE TABLE IF NOT EXISTS，可重复执行；也可跑 scripts/apply_campus_service_migration.py。
--   mysql -uroot school_rules_rag < sql/migrations/004_campus_services.sql

-- 1) 寝室报修：pending → processing → completed；pending/processing 可 closed
CREATE TABLE IF NOT EXISTS repair_requests (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  title VARCHAR(200) NOT NULL,
  description TEXT NOT NULL,
  category VARCHAR(32) NOT NULL DEFAULT 'other',
  building VARCHAR(100) NOT NULL,
  room VARCHAR(50) NOT NULL,
  contact VARCHAR(100) NOT NULL DEFAULT '',
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  handler_id INT NULL,
  handler_remark TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NULL,
  KEY idx_repair_user (user_id, status),
  KEY idx_repair_dedup (user_id, building, room, category, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2) 图书馆位置与预约
--    uk_slot 唯一键是防超卖的最终防线：并发请求最多一个成功，其余捕获 IntegrityError → 409
CREATE TABLE IF NOT EXISTS reservable_locations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  zone VARCHAR(50) NOT NULL DEFAULT '',
  capacity INT NOT NULL DEFAULT 1,
  is_active INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS location_reservations (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  location_id INT NOT NULL,
  user_id INT NOT NULL,
  res_date VARCHAR(10) NOT NULL,
  slot VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'reserved',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NULL,
  UNIQUE KEY uk_slot (location_id, res_date, slot),
  KEY idx_res_user (user_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3) 失物招领：pending_review → published / rejected → resolved；管理员可 removed
CREATE TABLE IF NOT EXISTS lost_found_items (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  kind VARCHAR(10) NOT NULL,
  title VARCHAR(200) NOT NULL,
  description TEXT NOT NULL,
  location VARCHAR(200) NOT NULL DEFAULT '',
  contact VARCHAR(100) NOT NULL DEFAULT '',
  image_urls TEXT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending_review',
  reviewer_id INT NULL,
  review_remark TEXT NULL,
  reviewed_at DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NULL,
  KEY idx_lf_status (status, created_at),
  KEY idx_lf_user (user_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 种子数据：给图书馆放几个可预约位置（幂等：只有空表才插入）
-- 名称带"图书馆"前缀，保证学生口语提问（"明天图书馆还有位置吗"）能按名称命中，
-- 见 005_location_naming.sql
INSERT INTO reservable_locations (name, zone, capacity, is_active)
SELECT * FROM (
  SELECT '图书馆一楼自习区 A 区' AS name, '自习区' AS zone, 40 AS capacity, 1 AS is_active
  UNION ALL SELECT '图书馆二楼研讨间 201', '研讨间', 6, 1
  UNION ALL SELECT '图书馆二楼研讨间 202', '研讨间', 6, 1
  UNION ALL SELECT '图书馆三楼电子阅览区', '电子阅览', 30, 1
) AS seed
WHERE NOT EXISTS (SELECT 1 FROM reservable_locations);
