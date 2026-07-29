CREATE TABLE IF NOT EXISTS conversation_turns (
    id CHAR(36) NOT NULL PRIMARY KEY,
    thread_id CHAR(36) NOT NULL,
    user_id CHAR(36) NOT NULL,
    client_turn_id VARCHAR(64) NOT NULL,
    request_fingerprint CHAR(64) NOT NULL,
    transport_origin VARCHAR(16) NOT NULL,
    client_idempotent BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(16) NOT NULL,
    lease_owner CHAR(36) NULL,
    lease_expires_at DATETIME(6) NULL,
    state_version_at_start BIGINT UNSIGNED NOT NULL DEFAULT 0,
    user_message_id CHAR(36) NULL,
    assistant_message_id CHAR(36) NULL,
    response_payload_json LONGTEXT NULL,
    normalized_error_category VARCHAR(80) NULL,
    observability_trace_id VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL,
    reserved_at DATETIME(6) NOT NULL,
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_conversation_turn_thread_client (thread_id, client_turn_id),
    KEY idx_conversation_turn_owner (user_id, thread_id),
    KEY idx_conversation_turn_status_lease (status, lease_expires_at),
    KEY idx_conversation_turn_completed (completed_at),
    CONSTRAINT fk_conversation_turn_thread
        FOREIGN KEY (thread_id) REFERENCES conversation_threads(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE conversation_turns
    ADD COLUMN transport_origin VARCHAR(16) NOT NULL DEFAULT 'legacy';

ALTER TABLE conversation_turns
    ADD COLUMN client_idempotent BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE conversation_turns
    ADD COLUMN state_version_at_start BIGINT UNSIGNED NOT NULL DEFAULT 0;

ALTER TABLE conversation_turns
    ADD COLUMN response_payload_json LONGTEXT NULL;

ALTER TABLE conversation_turns
    ADD COLUMN normalized_error_category VARCHAR(80) NULL;

ALTER TABLE conversation_turns
    ADD COLUMN observability_trace_id VARCHAR(64) NULL;

ALTER TABLE conversation_turns
    ADD COLUMN created_at DATETIME(6) NULL;

ALTER TABLE conversation_turns
    ADD COLUMN reserved_at DATETIME(6) NULL;

ALTER TABLE conversation_turns
    ADD COLUMN started_at DATETIME(6) NULL;

ALTER TABLE conversation_turns
    ADD KEY idx_conversation_turn_owner (user_id, thread_id);

ALTER TABLE conversation_turns
    ADD KEY idx_conversation_turn_completed (completed_at);

ALTER TABLE conversation_threads
    ADD COLUMN thread_state_version BIGINT UNSIGNED NOT NULL DEFAULT 0;

ALTER TABLE conversation_threads
    ADD COLUMN active_turn_id CHAR(36) NULL;

ALTER TABLE conversation_threads
    ADD KEY idx_conversation_threads_active_turn (active_turn_id);
