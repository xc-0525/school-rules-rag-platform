-- 公告模块：补分类与有效期字段
--
-- 背景：announcements 表原本只有 title/content/is_pinned/status，
-- 停水停电这类公告必须能"自动过期"，否则三天后还挂在列表上比不显示更糟。
--
-- 执行（MySQL 5.5 不支持 ADD COLUMN IF NOT EXISTS，重复执行请先跑
-- scripts/apply_notice_migration.py，它会先查 information_schema 再决定是否执行）：
--   mysql -uroot school_rules_rag < sql/migrations/002_notice_fields.sql

ALTER TABLE announcements
  ADD COLUMN category VARCHAR(32) NOT NULL DEFAULT 'other'
    COMMENT '分类：water/power/network/teaching/life/other',
  ADD COLUMN effective_at DATETIME NULL DEFAULT NULL
    COMMENT '生效时间，列表按此倒序',
  ADD COLUMN expires_at DATETIME NULL DEFAULT NULL
    COMMENT '失效时间，NULL=长期有效；过期后列表自动隐藏',
  ADD KEY idx_ann_time (expires_at, effective_at);

-- 历史数据回填：生效时间取创建时间
UPDATE announcements SET effective_at = created_at WHERE effective_at IS NULL;
