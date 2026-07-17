# Staging MySQL connection budget worksheet

Do **not** assume local recommendations (e.g. 8–16 pool slots) apply on the host.

## Measured host limits (fill in after connecting as staging user)

| Variable | Value | Source |
|---|---|---|
| `max_connections` | UNKNOWN | |
| Per-user connection limit | UNKNOWN | |
| `wait_timeout` | UNKNOWN | |
| `interactive_timeout` | UNKNOWN | |
| Observed connection error behavior | UNKNOWN | |

If `SHOW VARIABLES` is denied, leave **UNKNOWN** and keep a conservative web budget
(example starting point only — replace after measurement):

`web_capacity = workers × MYSQL_POOL_SIZE`

with `workers × MYSQL_POOL_SIZE + non_web_headroom + safety_reserve ≪` known limit,
or ≤ 20 total if limit is unknown on a shared host.

## Consumers inventory

| Consumer | Count / capacity | Notes |
|---|---|---|
| Gunicorn workers | | |
| Per-worker MySQL pool | | |
| Migration command | 1 (offline) | Stop web or keep pool tiny during migrate |
| Admin readiness | burst | Bounded checks only |
| Ingestion | 0 on staging by default | Keep off unless approved |
| Cleanup script | 0 scheduled | Dry-run only initially |
| Safety reserve | | |

## Formula

```
total_potential =
    web_workers × per_worker_pool
  + ingestion_admin_migration
  + safety_reserve
```

## Initial conservative proposal (placeholders until measured)

| Setting | Placeholder |
|---|---|
| `GUNICORN_WORKERS` | 2 |
| `GUNICORN_THREADS` | 2 |
| `MYSQL_POOL_SIZE` | 4 |
| Theoretical web capacity | 8 |
| Non-web headroom | 4 |
| Safety reserve | 4 |
| Assumed unknown host ceiling | treat 20 as soft cap until measured |

Record actual staging choices in the PR validation notes after host access exists.
