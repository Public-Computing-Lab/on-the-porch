# DreamHost Read-Only Audit Checklist

## Safety Rules

Run this checklist only after the project owner has authorized and established
DreamHost access. Work in `/home/pclabdev/source/on-the-porch`.

Do not pull, fetch, switch branches, edit or create files, restart or signal
processes, run migrations or ingestion, invoke the chatbot, call API endpoints,
change cron, or write to MySQL, Redis, or the vector store.

Before sharing output, redact usernames, hostnames, public IPs, emails, database
names if sensitive, connection strings, tokens, cookies, API keys, passwords,
and any environment value not explicitly listed as nonsecret.

## Repository

```text
pwd
git rev-parse --show-toplevel
git rev-parse HEAD
git status --short --branch
git log -1 --format='%H %cI %s'
```

Expected checkout SHA:
`840ec54dd1265b4f2f42cc18b66b54cbf2a99b89`.

## Host and Processes

```text
uptime
nproc
free -h
ps -eo pid,ppid,lstart,etime,rss,%mem,%cpu,args
ss -lntp
```

Redact unrelated command lines and private paths. Do not run `kill`, `pkill`, or
service-management commands.

Record:

- Gunicorn or Passenger command;
- worker class/count and thread count;
- timeout, graceful timeout, keepalive, and preload flags;
- process uptime and per-process RSS;
- host CPU and memory;
- listening ports and owning process;
- supervisor or restart mechanism if visible from the process tree.

## Static Frontend, Proxy, and SSE

Inspect only already-readable configuration files whose paths are known from
the active process or hosting panel. Do not recursively search home directories.
Record:

- static frontend serving mechanism;
- reverse proxy identity;
- proxy read/send timeout;
- buffering settings for event streams;
- connection limits.

Redact domains and certificate paths if sensitive. Mark panel-only settings
`EXTERNAL_CONFIGURATION_REQUIRED`.

## Schedules and Storage Metadata

```text
crontab -l
df -h
du -sh /home/pclabdev/source/on-the-porch
find logs -maxdepth 1 -type f -exec stat -f '%N %z %Sm' {} \;
```

On Linux, replace the final metadata command with:

```text
find logs -maxdepth 1 -type f -printf '%f %s %TY-%Tm-%TdT%TH:%TM:%TS\n'
```

Do not print log contents. Redact cron command arguments that include secrets.
Record only schedule, job category, and overlap risk.

## Nonsecret Configuration Names

List variable names without values:

```text
grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | cut -d= -f1 | sort
```

Do not share `.env` contents. The owner may separately record nonsecret values
for worker counts, pool sizes, provider names, and timeouts after redaction.

## MySQL Metadata

Use the deployment's existing protected client configuration. Never put a
password in shell history. Run only read-only metadata statements:

```sql
SELECT CURRENT_USER(), @@hostname, @@version;
SHOW GRANTS FOR CURRENT_USER();
SELECT table_name, table_rows, data_length, index_length
FROM information_schema.tables
WHERE table_schema = DATABASE()
ORDER BY data_length + index_length DESC
LIMIT 30;
SELECT table_name, index_name, non_unique,
       GROUP_CONCAT(column_name ORDER BY seq_in_index) AS columns
FROM information_schema.statistics
WHERE table_schema = DATABASE()
GROUP BY table_name, index_name, non_unique
ORDER BY table_name, index_name;
SHOW STATUS LIKE 'Threads_connected';
SHOW VARIABLES WHERE Variable_name IN
('max_connections', 'wait_timeout', 'innodb_buffer_pool_size');
```

Do not run full-table counts, `EXPLAIN ANALYZE`, writes, locks, migrations, or
schema changes. Redact host, user, database, and grant targets before sharing.

## Result Labels

Label every result:

- `CONFIRMED`: directly observed;
- `INFERRED`: supported indirectly and awaiting confirmation;
- `UNKNOWN`: not available;
- `EXTERNAL_CONFIGURATION_REQUIRED`: requires DreamHost panel information;
- `BLOCKED_BY_ACCESS`: access prevented inspection.
