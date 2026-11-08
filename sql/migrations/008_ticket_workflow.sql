-- 008：工单闭环（N-01）
--
-- 补处理人、处理意见、关闭时间三个字段，支撑 open → processing → closed 的状态机。
-- 幂等：由 backend/scripts/apply_extension_migration.py 按列存在性判断后执行。

ALTER TABLE tickets
  ADD COLUMN handler_id INT NULL DEFAULT NULL COMMENT '处理人（管理员用户 id）',
  ADD COLUMN handler_remark TEXT NULL COMMENT '处理意见（关闭时必填，学生可见）',
  ADD COLUMN closed_at DATETIME NULL DEFAULT NULL COMMENT '关闭时间';

ALTER TABLE tickets
  ADD INDEX idx_tickets_status_created (status, created_at);
