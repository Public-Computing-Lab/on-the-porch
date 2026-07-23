-- Public-beta provenance gating for source registry and document catalog.
-- Idempotent via db_migrations IGNORABLE_ERROR_CODES (1060/1061).

ALTER TABLE source_registry
    ADD COLUMN provenance_class VARCHAR(64) NOT NULL DEFAULT 'OFFICIAL_SECONDARY'
        AFTER source_type;

ALTER TABLE source_registry
    ADD COLUMN min_useful_documents INT NULL AFTER chunk_count;

ALTER TABLE civic_documents
    ADD COLUMN provenance_class VARCHAR(64) NOT NULL DEFAULT 'OFFICIAL_SECONDARY'
        AFTER doc_type;

ALTER TABLE civic_documents
    ADD INDEX idx_civic_documents_provenance (provenance_class);
