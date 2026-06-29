-- Shared API cache (session retrieval cache, rate limits, semantic cache)
-- when CACHE_BACKEND=mysql (production without Redis).

CREATE TABLE IF NOT EXISTS api_cache_entries (
  cache_key VARCHAR(191) NOT NULL PRIMARY KEY,
  cache_value MEDIUMTEXT NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  KEY idx_api_cache_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
