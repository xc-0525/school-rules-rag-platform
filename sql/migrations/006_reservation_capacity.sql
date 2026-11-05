-- 006：预约改为“按容量占用”（一个时段可被多人预约，直到满员）
--
-- 背景：0004 里的 uk_slot (location_id, res_date, slot) 把整个时段锁死给一个人，
-- 导致 capacity（自习区 40 / 研讨间 6）没有意义，也无法展示“剩余几个位置”。
--
-- 改法：去掉唯一键，改为普通联合索引；容量校验移到服务层，
-- 在事务内 SELECT ... FOR UPDATE 锁住位置行后计数，保证“查余量 + 插入”原子。
--
-- 幂等：以下两条由 backend/scripts/apply_campus_service_migration.py 按索引存在性判断后执行。

ALTER TABLE location_reservations DROP INDEX uk_slot;

ALTER TABLE location_reservations
  ADD KEY idx_res_slot (location_id, res_date, slot, status);

-- 研讨间按“整间”预约：容量记为 1；自习区/电子阅览仍按座位数（40 / 30）
UPDATE reservable_locations SET capacity = 1 WHERE zone = '研讨间' AND capacity <> 1;
