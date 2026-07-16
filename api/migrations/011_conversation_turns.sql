-- Conversation turn idempotency (client_turn_id scoped by owned thread).
-- Rollback: DROP TABLE IF EXISTS conversation_turns;

CREATE TABLE IF NOT EXISTS conversation_turns (
    id CHAR(36) NOT NULL PRIMARY KEY,
    thread_id CHAR(36) NOT NULL,
    user_id CHAR(36) NOT NULL,
    client_turn_id VARCHAR(64) NOT NULL,
    request_fingerprint CHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    lease_owner VARCHAR(64) NULL,
    lease_expires_at DATETIME(6) NULL,
    user_message_id CHAR(36) NULL,
    assistant_message_id CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    completed_at DATETIME(6) NULL,
    UNIQUE KEY uq_conversation_turns_thread_client (thread_id, client_turn_id),
    KEY idx_conversation_turns_user (user_id),
    KEY idx_conversation_turns_status_lease (status, lease_expires_at),
    CONSTRAINT fk_conversation_turns_thread
        FOREIGN KEY (thread_id) REFERENCES conversation_threads(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
