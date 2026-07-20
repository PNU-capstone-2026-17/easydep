-- EasyDep artifact store schema (MySQL 8.0+)
--
-- Reference DDL. The application creates the same structure through
-- app/db/models.py + init_db(), so this file is for DBA review, code review,
-- and manual provisioning of a deployment database.
--
-- Design notes
--   * Every row is scoped by app_id (UUID) so multiple users can work at once.
--   * artifact_type / phase are VARCHAR, not SQL ENUM: the roadmap keeps adding
--     artifact kinds (source code, IaC, test results) and ALTER TABLE on a hot
--     ENUM column is not worth it. Allowed values live in app/db/models.py.
--   * artifacts holds one row per (app_id, artifact_type) = "the current one".
--     artifact_versions holds the full revision history, so a feedback loop
--     never destroys the previous output.

CREATE DATABASE IF NOT EXISTS easydep
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE easydep;

-- One cloud-native application development session.
CREATE TABLE IF NOT EXISTS apps (
  app_id          VARCHAR(36)  NOT NULL COMMENT 'UUID issued at session start',
  owner_id        VARCHAR(64)  NOT NULL DEFAULT 'anonymous',
  title           VARCHAR(200) NOT NULL DEFAULT '',
  scenario_text   MEDIUMTEXT   NULL COMMENT 'user requirement input',
  current_stage   VARCHAR(32)  NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'IN_PROGRESS',
  created_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (app_id),
  KEY idx_apps_owner (owner_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Current artifact of each type for an app.
CREATE TABLE IF NOT EXISTS artifacts (
  id                 BIGINT       NOT NULL AUTO_INCREMENT,
  app_id             VARCHAR(36)  NOT NULL,
  phase              VARCHAR(20)  NOT NULL COMMENT 'REQUIREMENTS/DESIGN/IMPLEMENTATION/TESTING',
  artifact_type      VARCHAR(32)  NOT NULL COMMENT 'CLASS/SEQUENCE/API_SPEC/ERD/DEPLOYMENT/...',
  content_format     VARCHAR(16)  NOT NULL COMMENT 'PUML/JSON/TEXT/CODE',
  status             VARCHAR(16)  NOT NULL DEFAULT 'PENDING'
                       COMMENT 'PENDING/GENERATING/READY/FAILED',
  generation_started_at DATETIME(6) NULL
                       COMMENT 'when status became GENERATING; the lock is a lease that expires',
  current_version_id BIGINT       NULL COMMENT 'no FK: circular with artifact_versions',
  latest_version_no  INT          NOT NULL DEFAULT 0,
  created_at         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                       ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_artifacts_app_type (app_id, artifact_type),
  KEY idx_artifacts_current_version (current_version_id),
  CONSTRAINT fk_artifacts_app FOREIGN KEY (app_id)
    REFERENCES apps (app_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Every revision ever produced for an artifact.
CREATE TABLE IF NOT EXISTS artifact_versions (
  id            BIGINT      NOT NULL AUTO_INCREMENT,
  artifact_id   BIGINT      NOT NULL,
  version_no    INT         NOT NULL COMMENT '1-based, monotonic per artifact',
  content       LONGTEXT    NOT NULL COMMENT 'puml source, or JSON text for API spec',
  syntax_valid  TINYINT(1)  NULL,
  syntax_errors JSON        NULL COMMENT 'list of validation/compile error strings',
  origin        VARCHAR(20) NOT NULL DEFAULT 'GENERATED'
                  COMMENT 'GENERATED/AUTO_FIXED/FEEDBACK_REVISED',
  feedback_id   BIGINT      NULL,
  created_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_versions_artifact_no (artifact_id, version_no),
  CONSTRAINT fk_versions_artifact FOREIGN KEY (artifact_id)
    REFERENCES artifacts (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Natural-language feedback the user gave on an artifact.
CREATE TABLE IF NOT EXISTS feedbacks (
  id                BIGINT      NOT NULL AUTO_INCREMENT,
  app_id            VARCHAR(36) NOT NULL,
  artifact_type     VARCHAR(32) NOT NULL,
  feedback_text     TEXT        NOT NULL,
  status            VARCHAR(16) NOT NULL DEFAULT 'APPLIED'
                      COMMENT 'APPLIED/REJECTED/FAILED',
  result_version_id BIGINT      NULL COMMENT 'artifact_versions.id produced by this feedback',
  created_at        DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_feedbacks_app (app_id, created_at),
  CONSTRAINT fk_feedbacks_app FOREIGN KEY (app_id)
    REFERENCES apps (app_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
