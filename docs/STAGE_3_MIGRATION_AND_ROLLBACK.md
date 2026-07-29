# Stage 3 Migration and Rollback

## Migration

`api/migrations/010_conversation_turns.sql` creates `conversation_turns` and adds
`thread_state_version` plus `active_turn_id` to `conversation_threads`.

Before applying outside local development:

1. back up the conversation tables;
2. deploy the application version that understands the new columns;
3. run `api/db_migrations.py`;
4. verify the unique and status/lease indexes;
5. verify the application read/write identity can access the new coordination
   table;
6. separately configure the dynamic civic read-only identity described in
   `docs/DYNAMIC_SQL_SECURITY.md`.

The migration does not alter event, 311, crime, shooting, vector, or ingestion
tables. It does not create database users or grants.

## Local Verification

Migration 010 was applied to a fresh temporary schema in the local MySQL demo
container. The second migration run skipped the recorded version. The temporary
schema was dropped after verifying:

- `uq_conversation_turn_thread_client`
- `idx_conversation_turn_owner`
- `idx_conversation_turn_status_lease`
- `idx_conversation_turn_completed`
- all required turn columns

The existing `sentiment_demo` schema was also upgraded to verify compatibility
with an older donor-shaped `conversation_turns` table.

## Rollback

Application rollback must happen before schema rollback. While old application
code is running, the added columns/table may remain safely unused.

Manual schema rollback, only after confirming no rollback-era app depends on the
data:

```sql
ALTER TABLE conversation_threads
  DROP INDEX idx_conversation_threads_active_turn,
  DROP COLUMN active_turn_id,
  DROP COLUMN thread_state_version;

DROP TABLE conversation_turns;
```

Completed replay records and turn diagnostics are lost after the table drop.
Conversation messages and existing `thread_state_json` remain intact. There is no
automatic down-migration because the repository migration runner is forward-only.

Do not run this procedure against production without an approved maintenance and
backup plan.
