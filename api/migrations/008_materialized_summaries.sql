-- Iteration 5: pre-aggregated tables for fast Events API and prepared SQL paths.

CREATE TABLE IF NOT EXISTS events_materialized (
    id INT NOT NULL PRIMARY KEY,
    event_name VARCHAR(255) NULL,
    event_date VARCHAR(255) NULL,
    start_date DATE NULL,
    end_date DATE NULL,
    start_time TIME NULL,
    end_time TIME NULL,
    raw_text TEXT NULL,
    source_pdf VARCHAR(255) NULL,
    event_url VARCHAR(512) NULL,
    refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_events_mat_start (start_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mv_311_weekly (
    week_start DATE NOT NULL,
    issue_type VARCHAR(255) NOT NULL,
    request_count INT NOT NULL DEFAULT 0,
    refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (week_start, issue_type),
    INDEX idx_mv_311_week (week_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mv_crime_monthly (
    month_start DATE NOT NULL,
    offense_group VARCHAR(255) NOT NULL,
    incident_count INT NOT NULL DEFAULT 0,
    refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (month_start, offense_group),
    INDEX idx_mv_crime_month (month_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ingest_job_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    duration_seconds DOUBLE NULL,
    error_message TEXT NULL,
    INDEX idx_ingest_job_name_started (job_name, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
