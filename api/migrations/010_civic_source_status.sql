-- Civic source freshness status (311 / crime).
-- Rollback: DROP TABLE IF EXISTS civic_source_status;

CREATE TABLE IF NOT EXISTS civic_source_status (
    source_key VARCHAR(64) NOT NULL PRIMARY KEY,
    last_successful_ingest_at DATETIME NULL,
    data_through_date DATE NULL,
    latest_record_date DATE NULL,
    freshness_basis VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    record_count BIGINT NULL,
    status_updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_civic_source_status_updated (status_updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
