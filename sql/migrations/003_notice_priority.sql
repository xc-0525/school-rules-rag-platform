-- 公告模块：新增紧急级别
--
-- 背景：is_pinned 只控制列表排序，无法区分"置顶的常规通知"和
-- "停水停电这类必须让学生一进系统就看到"的紧急通知。
-- priority='urgent' 且 is_pinned=1 的已发布公告会出现在登录后的顶部横幅，
-- 学生可点 × 关闭（前端记录，公告板块仍然可见）。
--
-- MySQL 5.5 不支持 ADD COLUMN IF NOT EXISTS，重复执行请先跑
-- scripts/apply_notice_migration.py，它会先查 information_schema 再决定是否执行：
--   mysql -uroot school_rules_rag < sql/migrations/003_notice_priority.sql

ALTER TABLE announcements
  ADD COLUMN priority VARCHAR(16) NOT NULL DEFAULT 'normal'
    COMMENT 'normal=普通；urgent=紧急（登录后顶部横幅）',
  ADD KEY idx_ann_banner (priority, is_pinned, status, expires_at);

-- 历史数据保持 normal：不把存量置顶公告直接升级成紧急，避免误打扰
UPDATE announcements SET priority = 'normal' WHERE priority IS NULL OR priority = '';
