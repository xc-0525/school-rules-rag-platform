-- 007：预约取消原因
--
-- 管理员代取消某个学生的预约时填写原因，学生在“我的预约”列表看到该原因，
-- 相当于一次单人的定向通知（不引入额外站内信表）。
--
-- 幂等：由 backend/scripts/apply_campus_service_migration.py 按列存在性判断后执行。

ALTER TABLE location_reservations
  ADD COLUMN cancel_reason VARCHAR(200) NULL DEFAULT NULL COMMENT '取消原因（管理员填写，学生可见）';
