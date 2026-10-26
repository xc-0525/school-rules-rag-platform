-- ============================================================================
-- 校纪校规问答平台 · MySQL 初始化脚本
-- 兼容 MySQL 5.5+（本机为 5.5.11）与 MySQL 8.0：
--   * 不使用 JSON 类型（sources 用 MEDIUMTEXT 存 JSON 字符串）
--   * 排序规则用 utf8mb4_general_ci（5.5 可用，8.0 也兼容）
--   * 时间字段用 TIMESTAMP（5.5 才支持 DEFAULT/ON UPDATE CURRENT_TIMESTAMP）
-- 执行方式（任选）：
--   mysql -u root -p --default-character-set=utf8mb4 < sql/init_mysql.sql
--   Navicat / Trae 里打开本文件整体执行
-- ============================================================================

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS school_rules_rag
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_general_ci;

USE school_rules_rag;

-- ----------------------------------------------------------------------------
-- 1. 用户表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  username    VARCHAR(50)  NOT NULL COMMENT '登录名',
  password    VARCHAR(200) NOT NULL COMMENT 'PBKDF2-SHA256 哈希（见 app/core/security.py）',
  nickname    VARCHAR(50)  DEFAULT NULL COMMENT '昵称',
  role        ENUM('student','admin') NOT NULL DEFAULT 'student' COMMENT '角色',
  status      TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '1=正常 0=禁用',
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户表';

-- ----------------------------------------------------------------------------
-- 2. 会话表（一次连续多轮问答）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     INT UNSIGNED    NOT NULL,
  title       VARCHAR(100)    NOT NULL DEFAULT '新会话',
  created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME        NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_conv_user (user_id, updated_at),
  CONSTRAINT fk_conv_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='会话表';

-- ----------------------------------------------------------------------------
-- 3. 问答消息表（sources 存来源引用 JSON 字符串）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  conversation_id  BIGINT UNSIGNED NOT NULL,
  role             ENUM('user','assistant','system') NOT NULL COMMENT '消息角色',
  content          MEDIUMTEXT      NOT NULL COMMENT '消息正文',
  sources          MEDIUMTEXT      DEFAULT NULL COMMENT '来源引用 JSON 字符串',
  tokens           INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '消耗 token（暂未统计）',
  latency_ms       INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '生成耗时',
  refused          TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '1=防幻觉拒答',
  degraded         TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '1=引用校验降级',
  created_at       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_msg_conv (conversation_id, id),
  CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='问答记录表';

-- ----------------------------------------------------------------------------
-- 4. 校规文档表（PDF / Word / TXT）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id       INT UNSIGNED    NOT NULL COMMENT '上传人',
  title         VARCHAR(200)    NOT NULL COMMENT '文档标题',
  file_name     VARCHAR(255)    NOT NULL COMMENT '原始文件名',
  file_path     VARCHAR(500)    NOT NULL COMMENT '服务器存储路径',
  file_type     VARCHAR(20)     NOT NULL DEFAULT 'txt' COMMENT 'pdf/docx/txt',
  file_size     INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '字节数',
  chunk_count   INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '分块数量',
  status        ENUM('pending','processing','done','failed') NOT NULL DEFAULT 'pending' COMMENT '处理状态',
  error_message VARCHAR(500)    DEFAULT NULL COMMENT '失败原因',
  created_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME        NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_doc_status (status),
  KEY idx_doc_user (user_id),
  CONSTRAINT fk_doc_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='校规文档表';

-- ----------------------------------------------------------------------------
-- 5. 文档分块表（Qdrant 向量点映射，溯源链路的关键）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  document_id      BIGINT UNSIGNED NOT NULL,
  qdrant_point_id  VARCHAR(64)     DEFAULT NULL COMMENT 'Qdrant 向量点 ID',
  chapter          VARCHAR(200)    DEFAULT NULL COMMENT '章节',
  chunk_index      INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '块序号',
  content          TEXT            NOT NULL COMMENT '分块原文',
  created_at       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_chunks_qdrant (qdrant_point_id),
  KEY idx_chunks_doc (document_id, chunk_index),
  CONSTRAINT fk_chunks_doc FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='文档分块表（Qdrant 映射）';

-- ----------------------------------------------------------------------------
-- 6. 用户反馈表（点赞 / 点踩）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  message_id  BIGINT UNSIGNED NOT NULL,
  user_id     INT UNSIGNED    NOT NULL,
  rating      TINYINT         NOT NULL COMMENT '1=赞 -1=踩',
  comment     VARCHAR(500)    DEFAULT NULL,
  created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_feedback_user_msg (message_id, user_id),
  KEY idx_feedback_rating (rating),
  CONSTRAINT fk_fb_msg FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE CASCADE,
  CONSTRAINT fk_fb_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户反馈表';

-- ----------------------------------------------------------------------------
-- 7. 公告表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS announcements (
  id          INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  user_id     INT UNSIGNED    NOT NULL COMMENT '发布人（管理员）',
  title       VARCHAR(200)    NOT NULL,
  content     MEDIUMTEXT      NOT NULL,
  is_pinned   TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '1=置顶',
  status      ENUM('draft','published') NOT NULL DEFAULT 'draft',
  created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME        NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_ann_status (status, is_pinned),
  CONSTRAINT fk_ann_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='公告表';

-- ----------------------------------------------------------------------------
-- 8. 文档标签表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_tags (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name        VARCHAR(50)  NOT NULL COMMENT '标签名',
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_tags_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='文档标签表';

-- ----------------------------------------------------------------------------
-- 9. 文档-标签关联表（多对多）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_tag_relations (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  document_id BIGINT UNSIGNED NOT NULL,
  tag_id      INT UNSIGNED    NOT NULL,
  created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_doc_tag (document_id, tag_id),
  CONSTRAINT fk_dtr_doc FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE,
  CONSTRAINT fk_dtr_tag FOREIGN KEY (tag_id) REFERENCES document_tags (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='文档-标签关联表';

-- ============================================================================
-- 初始化数据
-- ============================================================================

-- 用户：admin / admin123（管理员），student / student123（学生）
-- 密码为 PBKDF2-SHA256，格式：pbkdf2_sha256$迭代次数$salt(base64)$hash(base64)
INSERT INTO users (username, password, nickname, role, status)
SELECT 'admin',
       'pbkdf2_sha256$120000$+9CXBlRIpa16zcV95Rmgmw==$b6Dl0J92N3ov3n16MK9VYAyIEFYfqkAt+BCwcwrTfVI=',
       '管理员', 'admin', 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'admin');

INSERT INTO users (username, password, nickname, role, status)
SELECT 'student',
       'pbkdf2_sha256$120000$vcem5ZikTGvNbg9WExbmdw==$gF0CvflQJgZIfZESYk1Fc9eiWuczlJ2VKX26WsFgpy0=',
       '演示学生', 'student', 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'student');

-- 默认文档标签
INSERT INTO document_tags (name)
SELECT name FROM (
  SELECT '学籍管理' AS name
  UNION ALL SELECT '违纪处分'
  UNION ALL SELECT '奖助学金'
  UNION ALL SELECT '宿舍管理'
  UNION ALL SELECT '教学管理'
  UNION ALL SELECT '校园安全'
) AS seed_tags
WHERE NOT EXISTS (SELECT 1 FROM document_tags WHERE document_tags.name = seed_tags.name);
