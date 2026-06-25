#!/usr/bin/env bash
# Monthly ingestion: Wayback Machine RSS backfill (slow, embedding-heavy).
# Schedule separately from daily cron_ingest.sh, e.g. first Sunday of the month.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cron_ingest_monthly.log"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  echo "[$(timestamp)] $*" | tee -a "$LOG_FILE"
}

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  PYTHON="$(command -v python)"
fi

if [ ! -f ".env" ]; then
  log "ERROR: Missing .env at $ROOT_DIR/.env"
  exit 1
fi

LOCK_FILE="$LOG_DIR/cron_ingest_monthly.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    log "Another monthly ingestion run is already in progress; exiting."
    exit 0
  fi
fi

log "Monthly ingestion job started"
log "Using Python: $PYTHON"

log "START: Wayback RSS backfill"
"$PYTHON" "on_the_porch/rag stuff/ingest_rss_wayback.py" 2>&1 | tee -a "$LOG_FILE"
log "DONE: Wayback RSS backfill"

log "Monthly ingestion job finished successfully"
