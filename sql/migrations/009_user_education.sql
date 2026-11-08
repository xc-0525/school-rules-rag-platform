-- 009：用户身份画像（N-20）
--
-- users 增加学历层次/学院/年级；documents 标注适用对象（本科/专科/通用）。
-- 存量用户默认 unset（不硬猜），首次登录引导补全。
-- 幂等：由 backend/scripts/apply_extension_migration.py 按列存在性判断后执行。

ALTER TABLE users
  ADD COLUMN education_level VARCHAR(16) NOT NULL DEFAULT 'unset'
    COMMENT 'undergraduate=本科 / junior=专科 / unset=未设置',
  ADD COLUMN college VARCHAR(64) NULL DEFAULT NULL COMMENT '学院',
  ADD COLUMN grade VARCHAR(16) NULL DEFAULT NULL COMMENT '年级，如 2024';

ALTER TABLE users
  ADD INDEX idx_users_education (education_level);

ALTER TABLE documents
  ADD COLUMN education_level VARCHAR(16) NOT NULL DEFAULT 'general'
    COMMENT 'undergraduate=本科 / junior=专科 / general=通用';
