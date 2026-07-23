-- Phase 5: authoritative source registry, document catalog, ingestion runs, operator audit.
-- Rollback: DROP TABLE IF EXISTS operator_audit_log, civic_document_chunks, civic_documents,
--           source_ingestion_runs, source_registry;

CREATE TABLE IF NOT EXISTS source_registry (
    source_id VARCHAR(128) NOT NULL PRIMARY KEY,
    family VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    authority VARCHAR(255) NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    publisher VARCHAR(255) NULL,
    official_url VARCHAR(1024) NULL,
    geography VARCHAR(128) NOT NULL DEFAULT 'Dorchester',
    update_frequency VARCHAR(64) NOT NULL DEFAULT 'unknown',
    ingestion_method VARCHAR(64) NOT NULL,
    content_format VARCHAR(64) NOT NULL DEFAULT 'mixed',
    freshness_policy VARCHAR(255) NULL,
    retention_policy VARCHAR(255) NULL,
    dedupe_key_template VARCHAR(255) NULL,
    parsing_strategy VARCHAR(64) NULL,
    indexing_eligible TINYINT(1) NOT NULL DEFAULT 0,
    citation_policy VARCHAR(255) NULL,
    limitations TEXT NULL,
    connector_id VARCHAR(64) NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'registered',
    last_successful_ingest_at DATETIME NULL,
    last_attempt_at DATETIME NULL,
    last_error VARCHAR(1024) NULL,
    index_version VARCHAR(64) NULL,
    chunk_count INT NULL,
    metadata_json JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_source_registry_family (family),
    INDEX idx_source_registry_enabled (enabled),
    INDEX idx_source_registry_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS source_ingestion_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_id VARCHAR(128) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    job_name VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    documents_seen INT NOT NULL DEFAULT 0,
    documents_upserted INT NOT NULL DEFAULT 0,
    chunks_upserted INT NOT NULL DEFAULT 0,
    error_message VARCHAR(1024) NULL,
    index_version VARCHAR(64) NULL,
    operator_user_id INT NULL,
    details_json JSON NULL,
    UNIQUE KEY uq_source_ingestion_run_id (run_id),
    INDEX idx_source_ingestion_source (source_id),
    INDEX idx_source_ingestion_status (status),
    INDEX idx_source_ingestion_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS civic_documents (
    document_id VARCHAR(160) NOT NULL PRIMARY KEY,
    source_id VARCHAR(128) NOT NULL,
    title VARCHAR(512) NOT NULL,
    publisher VARCHAR(255) NULL,
    official_url VARCHAR(1024) NULL,
    geography VARCHAR(128) NULL,
    publication_date DATE NULL,
    doc_type VARCHAR(64) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    index_version VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    limitations TEXT NULL,
    metadata_json JSON NULL,
    ingested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_civic_documents_source (source_id),
    INDEX idx_civic_documents_type (doc_type),
    INDEX idx_civic_documents_status (status),
    INDEX idx_civic_documents_sha (content_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS civic_document_chunks (
    chunk_id VARCHAR(200) NOT NULL PRIMARY KEY,
    document_id VARCHAR(160) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    ordinal INT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    vector_id VARCHAR(200) NULL,
    section_path VARCHAR(512) NULL,
    page_number INT NULL,
    char_count INT NOT NULL DEFAULT 0,
    index_version VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_civic_chunks_document (document_id),
    INDEX idx_civic_chunks_source (source_id),
    INDEX idx_civic_chunks_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS operator_audit_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    actor_user_id INT NULL,
    actor_email VARCHAR(255) NULL,
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(64) NOT NULL,
    target_id VARCHAR(160) NULL,
    details_json JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_operator_audit_created (created_at),
    INDEX idx_operator_audit_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
