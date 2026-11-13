#!/usr/bin/env bash
# Daily ingestion: civic data, events, RSS (current), Google Drive.
# Wayback RSS backfill runs monthly via cron_ingest_monthly.sh (slow + embed-heavy).
#
# DotNews / inprint disabled in this cron:
#   - run_dotnews_ingest_v2.py step (commented below)
#   - Dorchester Reporter RSS feed (commented in ingest_rss.py)
#   - Google Drive newsletter PDFs (PROCESS_NEWSLETTER_PDFS=false in google_drive_to_vectordb.py)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cron_ingest.log"

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

LOCK_FILE="$LOG_DIR/cron_ingest.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    log "Another ingestion run is already in progress; exiting."
    exit 0
  fi
else
  log "WARNING: 'flock' not available; skipping overlap protection."
fi

job_start() {
  local job_name="$1"
  "$PYTHON" "api/ingest_job_runner.py" start "$job_name" 2>/dev/null || true
}

job_finish() {
  local job_name="$1"
  local status="$2"
  local error_msg="${3:-}"
  if [ -n "$error_msg" ]; then
    "$PYTHON" "api/ingest_job_runner.py" finish "$job_name" "$status" --error "$error_msg" 2>/dev/null || true
  else
    "$PYTHON" "api/ingest_job_runner.py" finish "$job_name" "$status" 2>/dev/null || true
  fi
}

# label, job_name, allow_fail (true|false), command...
run_step() {
  local label="$1"
  local job_name="$2"
  local allow_fail="$3"
  shift 3

  local status=success
  local err_msg=""

  log "START: $label"
  job_start "$job_name"

  if ! "$@" 2>&1 | tee -a "$LOG_FILE"; then
    status=failed
    err_msg="$label failed"
    log "ERROR: $label"
    job_finish "$job_name" failed "$err_msg"
    if [ "$allow_fail" != "true" ]; then
      log "Cron ingestion aborted (required step failed)."
      exit 1
    fi
    return 0
  fi

  log "DONE: $label"
  job_finish "$job_name" success
}

log "Cron ingestion job started (daily)"
log "Using Python: $PYTHON"

run_step \
  "Boston Open Data sync (crime, 311, shootings)" \
  boston_sync \
  false \
  "$PYTHON" "on_the_porch/data_ingestion/boston_data_sync/boston_data_sync.py"

run_step \
  "Materialized summary refresh" \
  materialized_refresh \
  true \
  "$PYTHON" "scripts/refresh_materialized_views.py"

# DotNews / inprint PDF ingestion — disabled until dotnews.com feed is stable.
# run_step \
#   "DotNews newsletter ingestion" \
#   dotnews \
#   true \
#   "$PYTHON" "on_the_porch/data_ingestion/run_dotnews_ingest_v2.py"

run_step \
  "Tribe events ingestion" \
  tribe_events \
  true \
  "$PYTHON" "on_the_porch/data_ingestion/tribe_events_ingester.py" "all"

run_step \
  "RSS ingestion (current feeds)" \
  rss \
  true \
  "$PYTHON" "on_the_porch/rag stuff/ingest_rss.py"

run_step \
  "Google Drive ingestion" \
  google_drive \
  true \
  "$PYTHON" "on_the_porch/data_ingestion/google_drive_to_vectordb.py"

run_step \
  "Orphan guest user cleanup" \
  guest_cleanup \
  true \
  "$PYTHON" "api/cleanup_guest_users.py"

log "Cron ingestion job finished successfully"
