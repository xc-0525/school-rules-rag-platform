-- 010：反馈与未命中工作台（N-25）
--
-- 反馈加归因与处理状态，形成 pending → attributed → resolved → verified 闭环；
-- 未命中问题用独立的标记表，避免改动 messages 表结构。
-- 幂等：由 backend/scripts/apply_extension_migration.py 按列/表存在性判断后执行。

ALTER TABLE feedback
  ADD COLUMN attribution   VARCHAR(32)  NULL DEFAULT NULL COMMENT '语料缺失/答案错误/引用错误/问题不清',
  ADD COLUMN handle_status VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending/attributed/resolved/verified',
  ADD COLUMN handler_id    INT          NULL DEFAULT NULL,
  ADD COLUMN handled_at    DATETIME     NULL DEFAULT NULL,
  ADD COLUMN handle_remark VARCHAR(500) NULL DEFAULT NULL;

ALTER TABLE feedback
  ADD INDEX idx_feedback_status (handle_status, created_at);

CREATE TABLE IF NOT EXISTS unmatched_marks (
  query_hash   CHAR(40)     NOT NULL COMMENT '归一化问题的 sha1',
  query_text   VARCHAR(500) NOT NULL COMMENT '代表性原文',
  status       VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending/ignored/resolved',
  remark       VARCHAR(500) NULL DEFAULT NULL,
  handler_id   INT          NULL DEFAULT NULL,
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NULL DEFAULT NULL,
  PRIMARY KEY (query_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='未命中问题处理标记';
