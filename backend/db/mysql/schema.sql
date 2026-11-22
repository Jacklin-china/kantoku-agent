-- Kantoku · MySQL 8 镜像库 schema
-- 由 data/kantoku.db 的 PRAGMA 元数据生成，共 35 张表
-- 生成脚本：scripts/gen_mysql_schema.py
-- 注意：文本列按 UTF8MB4 存储；被键引用的文本列降为 VARCHAR(191)；
--       SQLite 的 CHECK 约束未迁移，业务校验仍由应用侧完成。

CREATE DATABASE IF NOT EXISTS `kantoku`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `kantoku`;
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS `approvals`;
CREATE TABLE `approvals` (
  `id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `run_id` VARCHAR(191) NOT NULL,
  `node_id` LONGTEXT NOT NULL,
  `decision` VARCHAR(191) NOT NULL,
  `request_json` LONGTEXT NOT NULL DEFAULT ('{}'),
  `response_json` LONGTEXT NOT NULL DEFAULT ('{}'),
  `created_at` VARCHAR(191) NOT NULL,
  `decided_at` VARCHAR(64),
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_approvals_pending` ON `approvals` (`decision`, `created_at`);
DROP TABLE IF EXISTS `archive_asset`;
CREATE TABLE `archive_asset` (
  `source_request_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `project` VARCHAR(191) NOT NULL,
  `episode` VARCHAR(191) NOT NULL,
  `shot_no` BIGINT NOT NULL,
  `characters_json` LONGTEXT NOT NULL,
  `image_path` LONGTEXT NOT NULL,
  `metadata_path` LONGTEXT NOT NULL,
  `image_sha256` LONGTEXT NOT NULL,
  `approved` BIGINT NOT NULL,
  `created_at` VARCHAR(191) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_archive_scope` ON `archive_asset` (`project`, `episode`, `shot_no`, `approved`, `created_at`);
DROP TABLE IF EXISTS `artifacts`;
CREATE TABLE `artifacts` (
  `id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `type` LONGTEXT NOT NULL,
  `run_id` VARCHAR(191),
  `conversation_id` VARCHAR(191),
  `node_id` LONGTEXT NOT NULL,
  `source` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `created_at` VARCHAR(191) NOT NULL,
  `metadata_json` LONGTEXT NOT NULL DEFAULT ('{}'),
  `location` LONGTEXT,
  `version` BIGINT NOT NULL DEFAULT 1,
  `lifecycle_state` VARCHAR(191) NOT NULL DEFAULT 'active',
  `lifecycle_version` BIGINT NOT NULL DEFAULT 1,
  `lifecycle_updated_at` VARCHAR(64),
  FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`),
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_artifacts_lifecycle` ON `artifacts` (`lifecycle_state`, `created_at`);
CREATE INDEX `idx_artifacts_conversation` ON `artifacts` (`conversation_id`, `created_at`);
CREATE INDEX `idx_artifacts_run` ON `artifacts` (`run_id`, `created_at`);
DROP TABLE IF EXISTS `auth_owner`;
CREATE TABLE `auth_owner` (
  `id` BIGINT NOT NULL PRIMARY KEY,
  `username` LONGTEXT NOT NULL,
  `salt` LONGBLOB NOT NULL,
  `password_digest` LONGBLOB NOT NULL,
  `failed_attempts` BIGINT NOT NULL DEFAULT 0,
  `locked_until` BIGINT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `auth_sessions`;
CREATE TABLE `auth_sessions` (
  `digest` VARCHAR(191) NOT NULL PRIMARY KEY,
  `created_at` BIGINT NOT NULL,
  `expires_at` BIGINT NOT NULL,
  `revoked` BIGINT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `batch_runs`;
CREATE TABLE `batch_runs` (
  `batch_id` VARCHAR(191) NOT NULL,
  `run_id` VARCHAR(191) NOT NULL,
  `position` BIGINT NOT NULL,
  PRIMARY KEY (`batch_id`, `run_id`),
  UNIQUE KEY (`batch_id`, `position`),
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`),
  FOREIGN KEY (`batch_id`) REFERENCES `batches` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_batch_runs_run` ON `batch_runs` (`run_id`);
DROP TABLE IF EXISTS `batches`;
CREATE TABLE `batches` (
  `id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `name` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `concurrency_limit` BIGINT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  `version` BIGINT NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `checkpoints`;
CREATE TABLE `checkpoints` (
  `id` BIGINT NOT NULL PRIMARY KEY,
  `run_id` VARCHAR(191) NOT NULL,
  `node_id` LONGTEXT NOT NULL,
  `next_node` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `state_json` LONGTEXT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL,
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_checkpoints_run` ON `checkpoints` (`run_id`, `id`);
DROP TABLE IF EXISTS `comic_assets`;
CREATE TABLE `comic_assets` (
  `asset_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `project_id` VARCHAR(191) NOT NULL,
  `kind` VARCHAR(191) NOT NULL,
  `name` LONGTEXT NOT NULL,
  `current_version` BIGINT NOT NULL,
  `state` VARCHAR(191) NOT NULL,
  `pinned_version` BIGINT,
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  FOREIGN KEY (`project_id`) REFERENCES `comic_projects` (`project_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_comic_assets_project` ON `comic_assets` (`project_id`, `kind`, `state`);
DROP TABLE IF EXISTS `comic_entity_versions`;
CREATE TABLE `comic_entity_versions` (
  `project_id` VARCHAR(191) NOT NULL,
  `entity_type` VARCHAR(191) NOT NULL,
  `entity_id` VARCHAR(191) NOT NULL,
  `version` BIGINT NOT NULL,
  `payload_json` LONGTEXT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`project_id`, `entity_type`, `entity_id`, `version`),
  FOREIGN KEY (`project_id`) REFERENCES `comic_projects` (`project_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_comic_entity_latest` ON `comic_entity_versions` (`project_id`, `entity_type`, `entity_id`, `version`);
DROP TABLE IF EXISTS `comic_projects`;
CREATE TABLE `comic_projects` (
  `project_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `title` LONGTEXT NOT NULL,
  `description` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `current_version` BIGINT NOT NULL,
  `brief_id` LONGTEXT NOT NULL,
  `brief_version` BIGINT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  `director_id` LONGTEXT,
  `director_version` BIGINT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `comic_prompts`;
CREATE TABLE `comic_prompts` (
  `prompt_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `project_id` VARCHAR(191) NOT NULL,
  `storyboard_id` VARCHAR(191) NOT NULL,
  `shot_id` VARCHAR(191) NOT NULL,
  `current_version` BIGINT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  UNIQUE KEY (`shot_id`),
  FOREIGN KEY (`shot_id`) REFERENCES `comic_shots` (`shot_id`),
  FOREIGN KEY (`storyboard_id`) REFERENCES `comic_storyboards` (`storyboard_id`),
  FOREIGN KEY (`project_id`) REFERENCES `comic_projects` (`project_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_comic_prompts_project` ON `comic_prompts` (`project_id`);
DROP TABLE IF EXISTS `comic_schema_migrations`;
CREATE TABLE `comic_schema_migrations` (
  `version` BIGINT NOT NULL PRIMARY KEY,
  `applied_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `comic_shots`;
CREATE TABLE `comic_shots` (
  `shot_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `storyboard_id` VARCHAR(191) NOT NULL,
  `project_id` VARCHAR(191) NOT NULL,
  `current_version` BIGINT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  FOREIGN KEY (`project_id`) REFERENCES `comic_projects` (`project_id`),
  FOREIGN KEY (`storyboard_id`) REFERENCES `comic_storyboards` (`storyboard_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_comic_shots_storyboard` ON `comic_shots` (`storyboard_id`);
DROP TABLE IF EXISTS `comic_storyboards`;
CREATE TABLE `comic_storyboards` (
  `storyboard_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `project_id` VARCHAR(191) NOT NULL,
  `current_version` BIGINT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  FOREIGN KEY (`project_id`) REFERENCES `comic_projects` (`project_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_comic_storyboards_project` ON `comic_storyboards` (`project_id`);
DROP TABLE IF EXISTS `conversation_generation_requests`;
CREATE TABLE `conversation_generation_requests` (
  `generation_request_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `conversation_id` VARCHAR(191) NOT NULL,
  `user_message_id` VARCHAR(191) NOT NULL,
  `prompt` LONGTEXT NOT NULL,
  `created_at` VARCHAR(191) NOT NULL,
  `context_json` LONGTEXT NOT NULL DEFAULT ('{}'),
  `reference_artifact_id` VARCHAR(191),
  FOREIGN KEY (`user_message_id`) REFERENCES `conversation_messages` (`id`),
  FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`),
  FOREIGN KEY (`reference_artifact_id`) REFERENCES `artifacts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_conversation_generations` ON `conversation_generation_requests` (`conversation_id`, `created_at`);
DROP TABLE IF EXISTS `conversation_messages`;
CREATE TABLE `conversation_messages` (
  `id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `conversation_id` VARCHAR(191) NOT NULL,
  `role` LONGTEXT NOT NULL,
  `type` LONGTEXT NOT NULL,
  `content` LONGTEXT NOT NULL,
  `run_id` VARCHAR(191),
  `event_id` VARCHAR(191),
  `created_at` VARCHAR(191) NOT NULL,
  `artifact_id` VARCHAR(191),
  UNIQUE KEY (`conversation_id`, `event_id`),
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`),
  FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`),
  FOREIGN KEY (`artifact_id`) REFERENCES `artifacts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_conversation_messages` ON `conversation_messages` (`conversation_id`, `created_at`);
DROP TABLE IF EXISTS `conversations`;
CREATE TABLE `conversations` (
  `id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `title` LONGTEXT NOT NULL,
  `interaction_mode` LONGTEXT NOT NULL,
  `domain` LONGTEXT,
  `active_run_id` VARCHAR(191),
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(191) NOT NULL,
  `deleted_at` VARCHAR(191),
  `fast_domain_task_id` LONGTEXT,
  FOREIGN KEY (`active_run_id`) REFERENCES `runs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_conversations_visible` ON `conversations` (`deleted_at`, `updated_at`);
CREATE INDEX `idx_conversations_updated` ON `conversations` (`updated_at`);
DROP TABLE IF EXISTS `core_schema_migrations`;
CREATE TABLE `core_schema_migrations` (
  `version` BIGINT NOT NULL PRIMARY KEY,
  `applied_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `image_result`;
CREATE TABLE `image_result` (
  `reservation_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `result_json` LONGTEXT NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `ledger`;
CREATE TABLE `ledger` (
  `id` BIGINT NOT NULL PRIMARY KEY,
  `reservation_id` VARCHAR(191) NOT NULL,
  `job` LONGTEXT NOT NULL,
  `project` VARCHAR(191) NOT NULL,
  `episode` VARCHAR(191) NOT NULL,
  `shot_no` BIGINT NOT NULL,
  `kind` LONGTEXT NOT NULL,
  `est_fen` BIGINT NOT NULL,
  `actual_fen` BIGINT,
  `model` LONGTEXT NOT NULL,
  `provider_job_id` LONGTEXT,
  `status` LONGTEXT NOT NULL,
  `created_at` VARCHAR(191) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  `run_id` LONGTEXT,
  `provider` LONGTEXT,
  `idempotency_key` LONGTEXT,
  `artifact_id` LONGTEXT,
  `conversation_id` VARCHAR(191),
  UNIQUE KEY (`reservation_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_ledger_conversation` ON `ledger` (`conversation_id`, `created_at`);
CREATE INDEX `idx_ledger_budget_scope` ON `ledger` (`project`, `episode`, `shot_no`, `created_at`);
DROP TABLE IF EXISTS `media_jobs`;
CREATE TABLE `media_jobs` (
  `generation_request_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `conversation_id` VARCHAR(191) NOT NULL,
  `user_message_id` VARCHAR(191) NOT NULL,
  `media_type` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `artifact_id` VARCHAR(191),
  `error_id` LONGTEXT,
  `error_message` LONGTEXT,
  `created_at` VARCHAR(191) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  `estimate_fen` BIGINT,
  `approval_status` LONGTEXT,
  FOREIGN KEY (`artifact_id`) REFERENCES `artifacts` (`id`),
  FOREIGN KEY (`user_message_id`) REFERENCES `conversation_messages` (`id`),
  FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_media_jobs_conversation` ON `media_jobs` (`conversation_id`, `created_at`);
DROP TABLE IF EXISTS `node_executions`;
CREATE TABLE `node_executions` (
  `run_id` VARCHAR(191) NOT NULL,
  `node_id` VARCHAR(191) NOT NULL,
  `status` LONGTEXT NOT NULL,
  `started_at` VARCHAR(64),
  `completed_at` VARCHAR(64),
  `retry_count` BIGINT NOT NULL DEFAULT 0,
  `error` LONGTEXT,
  `outputs_json` LONGTEXT NOT NULL DEFAULT ('{}'),
  PRIMARY KEY (`run_id`, `node_id`),
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `persona`;
CREATE TABLE `persona` (
  `name` VARCHAR(191) NOT NULL PRIMARY KEY,
  `appearance` LONGTEXT NOT NULL,
  `outfit` LONGTEXT NOT NULL,
  `style_tokens_json` LONGTEXT NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `qc_prediction`;
CREATE TABLE `qc_prediction` (
  `source_request_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `result_json` LONGTEXT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `qc_review`;
CREATE TABLE `qc_review` (
  `source_request_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `label_json` LONGTEXT NOT NULL,
  `approved` BIGINT NOT NULL,
  `created_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `recipe`;
CREATE TABLE `recipe` (
  `id` BIGINT NOT NULL PRIMARY KEY,
  `episode` VARCHAR(191) NOT NULL,
  `shot_no` BIGINT NOT NULL,
  `prompt_version` VARCHAR(191) NOT NULL,
  `prompt` LONGTEXT NOT NULL,
  `prompt_sha256` LONGTEXT NOT NULL,
  `model` LONGTEXT NOT NULL,
  `size` LONGTEXT NOT NULL,
  `reference_asset_ids_json` LONGTEXT NOT NULL,
  `seed` BIGINT,
  `created_at` VARCHAR(191) NOT NULL,
  UNIQUE KEY (`episode`, `shot_no`, `prompt_version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_recipe_episode_shot` ON `recipe` (`episode`, `shot_no`, `created_at`);
DROP TABLE IF EXISTS `rework_queue`;
CREATE TABLE `rework_queue` (
  `source_request_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `failure_reasons_json` LONGTEXT NOT NULL,
  `reason` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL DEFAULT ('pending'),
  `target_request_id` LONGTEXT,
  `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `run_events`;
CREATE TABLE `run_events` (
  `id` BIGINT NOT NULL PRIMARY KEY,
  `run_id` VARCHAR(191) NOT NULL,
  `sequence` BIGINT NOT NULL,
  `event_type` LONGTEXT NOT NULL,
  `node_id` LONGTEXT,
  `payload_json` LONGTEXT NOT NULL DEFAULT ('{}'),
  `created_at` VARCHAR(64) NOT NULL,
  UNIQUE KEY (`run_id`, `sequence`),
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_run_events_stream` ON `run_events` (`run_id`, `sequence`);
DROP TABLE IF EXISTS `runs`;
CREATE TABLE `runs` (
  `id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `domain` LONGTEXT NOT NULL,
  `workflow` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `state_json` LONGTEXT NOT NULL,
  `current_node` LONGTEXT NOT NULL,
  `started_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL,
  `completed_at` VARCHAR(64),
  `error` LONGTEXT,
  `cost_fen` BIGINT NOT NULL DEFAULT 0,
  `interaction_mode` LONGTEXT NOT NULL DEFAULT ('guided')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `runtime_file_leases`;
CREATE TABLE `runtime_file_leases` (
  `lease_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `file_id` LONGTEXT NOT NULL,
  `pid` BIGINT NOT NULL,
  `expire_at` DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `runtime_files`;
CREATE TABLE `runtime_files` (
  `file_id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `path` VARCHAR(191) NOT NULL,
  `type` LONGTEXT NOT NULL,
  `owner` LONGTEXT NOT NULL,
  `created_at` DOUBLE NOT NULL,
  `expire_at` DOUBLE,
  `status` VARCHAR(191) NOT NULL,
  `touched_at` DOUBLE NOT NULL,
  `version` BIGINT NOT NULL DEFAULT 1,
  UNIQUE KEY (`path`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `runtime_expiry` ON `runtime_files` (`status`, `expire_at`);
DROP TABLE IF EXISTS `shot`;
CREATE TABLE `shot` (
  `id` BIGINT NOT NULL PRIMARY KEY,
  `episode` VARCHAR(191) NOT NULL,
  `shot_no` BIGINT NOT NULL,
  `desc` LONGTEXT NOT NULL,
  `dialogue` LONGTEXT NOT NULL,
  `camera` LONGTEXT NOT NULL,
  `duration_s` BIGINT NOT NULL,
  `characters_json` LONGTEXT NOT NULL,
  UNIQUE KEY (`episode`, `shot_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX `idx_shot_episode_no` ON `shot` (`episode`, `shot_no`);
DROP TABLE IF EXISTS `skill_executions`;
CREATE TABLE `skill_executions` (
  `id` VARCHAR(191) NOT NULL PRIMARY KEY,
  `run_id` VARCHAR(191) NOT NULL,
  `node_id` LONGTEXT NOT NULL,
  `skill_id` LONGTEXT NOT NULL,
  `status` LONGTEXT NOT NULL,
  `started_at` VARCHAR(64) NOT NULL,
  `completed_at` VARCHAR(64),
  `error` LONGTEXT,
  `outputs_json` LONGTEXT NOT NULL DEFAULT ('{}'),
  FOREIGN KEY (`run_id`) REFERENCES `runs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
DROP TABLE IF EXISTS `trace`;
CREATE TABLE `trace` (
  `id` BIGINT NOT NULL PRIMARY KEY,
  `ts` LONGTEXT NOT NULL,
  `kind` LONGTEXT NOT NULL,
  `model` LONGTEXT,
  `in_tokens` BIGINT NOT NULL,
  `out_tokens` BIGINT NOT NULL,
  `latency_ms` BIGINT NOT NULL,
  `cost_fen` BIGINT,
  `ok` BIGINT NOT NULL,
  `shot_no` BIGINT,
  `error` LONGTEXT,
  `usage_reported` BIGINT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET FOREIGN_KEY_CHECKS = 1;
