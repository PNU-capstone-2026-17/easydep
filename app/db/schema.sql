-- EasyDep artifact store schema (MySQL 8.0+)
--
-- Reference DDL. The application creates the same structure through
-- app/db/models.py + init_db() and upgrades an existing database through
-- app/db/migrations.py. This file is for DBA review and manual provisioning.
--
-- Design notes
--   * Every row is scoped by app_id (UUID) so multiple users can work at once.
--   * artifact_type is VARCHAR, not SQL ENUM: the roadmap keeps adding artifact
--     kinds (source code, IaC, test results) and ALTER TABLE on a hot ENUM
--     column is not worth it. Allowed values live in app/db/models.py.
--   * artifacts holds one row per (app_id, artifact_type) = "the current one".
--     artifact_versions holds the full revision history, so a feedback loop
--     never destroys the previous output.
--   * latest_version_no=0 means not produced; positive values address the current
--     row through (artifact_id, version_no), so there is no second ID pointer.
--   * Columns are limited to what the application actually reads. Anything
--     speculative was left out; it can be added with ALTER TABLE when a feature
--     needs it.

CREATE DATABASE IF NOT EXISTS easydep
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE easydep;

-- Applied startup migrations. A fresh database starts empty; init_db records the
-- current revision after confirming that the following schema is already present.
CREATE TABLE IF NOT EXISTS schema_migrations (
  revision   VARCHAR(64) NOT NULL,
  applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One cloud-native application development session.
CREATE TABLE IF NOT EXISTS apps (
  app_id                    VARCHAR(36) NOT NULL COMMENT 'UUID issued at session start',
  requirements_text         MEDIUMTEXT  NULL COMMENT 'natural language requirements as the user wrote them',
  resource_constraints_text MEDIUMTEXT  NULL COMMENT 'cloud resource constraints as the user wrote them',
  current_stage             VARCHAR(32) NULL COMMENT 'stage whose artifact was written last',
  created_at                DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (app_id),
  KEY ix_apps_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Deployment alternatives selected while application requirements are analyzed.
CREATE TABLE IF NOT EXISTS deployment_preferences (
  app_id       VARCHAR(36) NOT NULL,
  selection    JSON        NOT NULL,
  created_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                              ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (app_id),
  CONSTRAINT fk_deployment_preferences_app FOREIGN KEY (app_id)
    REFERENCES apps (app_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Current artifact of each type for an app. Concurrent writers serialize on the
-- parent apps row rather than storing an otherwise-unused lease column here.
CREATE TABLE IF NOT EXISTS artifacts (
  id                    BIGINT      NOT NULL AUTO_INCREMENT,
  app_id                VARCHAR(36) NOT NULL,
  artifact_type         VARCHAR(32) NOT NULL COMMENT 'REFINE_REQ/USECASE_SPEC/CLASS/SEQUENCE/...',
  latest_version_no     INT         NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uq_artifacts_app_type (app_id, artifact_type),
  CONSTRAINT ck_artifacts_latest_version_nonnegative
    CHECK (latest_version_no >= 0),
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
                  COMMENT 'GENERATED/AUTO_FIXED/FEEDBACK_REVISED/IMPORTED',
  created_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_versions_artifact_no (artifact_id, version_no),
  CONSTRAINT ck_versions_version_positive CHECK (version_no > 0),
  CONSTRAINT fk_versions_artifact FOREIGN KEY (artifact_id)
    REFERENCES artifacts (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Immutable file tree belonging to one implementation artifact version.
CREATE TABLE IF NOT EXISTS artifact_files (
  artifact_version_id BIGINT        NOT NULL,
  file_path           VARCHAR(512) CHARACTER SET utf8mb4
                      COLLATE utf8mb4_0900_as_cs NOT NULL,
  content             LONGTEXT      NOT NULL,
  sha256              CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  PRIMARY KEY (artifact_version_id, file_path),
  CONSTRAINT ck_artifact_files_path CHECK (CHAR_LENGTH(file_path) > 0),
  CONSTRAINT ck_artifact_files_sha256 CHECK (sha256 REGEXP '^[0-9a-f]{64}$'),
  CONSTRAINT fk_artifact_files_version FOREIGN KEY (artifact_version_id)
    REFERENCES artifact_versions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- User-visible commands. The app row is locked before the active-command check,
-- so two concurrent requests cannot both become active for the same app.
CREATE TABLE IF NOT EXISTS workspace_commands (
  command_id  VARCHAR(36) NOT NULL,
  app_id      VARCHAR(36) NOT NULL,
  action      VARCHAR(48) NOT NULL,
  stage       VARCHAR(32) NOT NULL,
  status      VARCHAR(24) NOT NULL,
  payload     JSON        NOT NULL,
  result      JSON        NULL,
  error       LONGTEXT    NULL,
  created_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at  DATETIME(6) NULL,
  completed_at DATETIME(6) NULL,
  PRIMARY KEY (command_id),
  UNIQUE KEY uq_workspace_commands_app_command (app_id, command_id),
  KEY ix_workspace_commands_app_created (app_id, created_at),
  KEY ix_workspace_commands_app_status (app_id, status),
  KEY ix_workspace_commands_status (status),
  CONSTRAINT fk_workspace_commands_app FOREIGN KEY (app_id)
    REFERENCES apps (app_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Append-only workspace timeline. command_id may be NULL for app-level events;
-- when present, the composite FK prevents a cross-app command reference.
CREATE TABLE IF NOT EXISTS workspace_events (
  event_id   BIGINT      NOT NULL AUTO_INCREMENT,
  app_id     VARCHAR(36) NOT NULL,
  command_id VARCHAR(36) NULL,
  stage      VARCHAR(32) NOT NULL,
  kind       VARCHAR(32) NOT NULL,
  actor      VARCHAR(16) NOT NULL,
  text       MEDIUMTEXT  NOT NULL,
  metadata   JSON        NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (event_id),
  KEY ix_workspace_events_app_command (app_id, command_id),
  KEY ix_workspace_events_app_event (app_id, event_id),
  CONSTRAINT fk_workspace_events_app FOREIGN KEY (app_id)
    REFERENCES apps (app_id) ON DELETE CASCADE,
  CONSTRAINT fk_workspace_events_command FOREIGN KEY (app_id, command_id)
    REFERENCES workspace_commands (app_id, command_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Requirements checkpoint skeleton, channel values, pending writes, and graph mode.
CREATE TABLE IF NOT EXISTS requirements_checkpoints (
  thread_id             VARCHAR(128) NOT NULL,
  checkpoint_ns         VARCHAR(128) NOT NULL DEFAULT '',
  checkpoint_id         VARCHAR(128) NOT NULL,
  parent_checkpoint_id  VARCHAR(128) NULL,
  checkpoint_type       VARCHAR(32)  NOT NULL,
  checkpoint            LONGBLOB     NOT NULL,
  metadata_type         VARCHAR(32)  NOT NULL,
  checkpoint_metadata   LONGBLOB     NOT NULL,
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id),
  KEY ix_requirements_checkpoints_checkpoint_id (checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS requirements_checkpoint_blobs (
  thread_id     VARCHAR(128) NOT NULL,
  checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
  channel       VARCHAR(255) NOT NULL,
  version       VARCHAR(64)  NOT NULL,
  blob_type     VARCHAR(32)  NOT NULL,
  blob          LONGBLOB     NULL,
  PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS requirements_checkpoint_writes (
  thread_id     VARCHAR(128) NOT NULL,
  checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
  checkpoint_id VARCHAR(128) NOT NULL,
  task_id       VARCHAR(128) NOT NULL,
  idx           INT          NOT NULL,
  channel       VARCHAR(255) NOT NULL,
  write_type    VARCHAR(32)  NOT NULL,
  blob          LONGBLOB     NOT NULL,
  task_path     VARCHAR(255) NOT NULL DEFAULT '',
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS requirements_sessions (
  thread_id VARCHAR(128) NOT NULL,
  gated     TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (thread_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Design uses the same LangGraph storage contract with a separate namespace.
CREATE TABLE IF NOT EXISTS design_checkpoints (
  thread_id             VARCHAR(128) NOT NULL,
  checkpoint_ns         VARCHAR(128) NOT NULL DEFAULT '',
  checkpoint_id         VARCHAR(128) NOT NULL,
  parent_checkpoint_id  VARCHAR(128) NULL,
  checkpoint_type       VARCHAR(32)  NOT NULL,
  checkpoint            LONGBLOB     NOT NULL,
  metadata_type         VARCHAR(32)  NOT NULL,
  checkpoint_metadata   LONGBLOB     NOT NULL,
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id),
  KEY ix_design_checkpoints_checkpoint_id (checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS design_checkpoint_blobs (
  thread_id     VARCHAR(128) NOT NULL,
  checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
  channel       VARCHAR(255) NOT NULL,
  version       VARCHAR(64)  NOT NULL,
  blob_type     VARCHAR(32)  NOT NULL,
  blob          LONGBLOB     NULL,
  PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS design_checkpoint_writes (
  thread_id     VARCHAR(128) NOT NULL,
  checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
  checkpoint_id VARCHAR(128) NOT NULL,
  task_id       VARCHAR(128) NOT NULL,
  idx           INT          NOT NULL,
  channel       VARCHAR(255) NOT NULL,
  write_type    VARCHAR(32)  NOT NULL,
  blob          LONGBLOB     NOT NULL,
  task_path     VARCHAR(255) NOT NULL DEFAULT '',
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
