#!/usr/bin/env bash
# Operator/DBA backup + isolated restore drill for On the Porch.
# The application runtime user (e.g. demo_user) is intentionally least-privilege.
# This script must be run with a separate DBA/operator role that can:
#   - SELECT / LOCK TABLES / SHOW VIEW on the source database
#   - CREATE / DROP DATABASE for a disposable restore target
#   - CREATE / INSERT / INDEX on the restore target
#
# Usage:
#   export OTP_DBA_USER=... OTP_DBA_PASSWORD=... OTP_DBA_HOST=127.0.0.1
#   export MYSQL_DB=sentiment_demo
#   ./scripts/staging/backup_restore_drill.sh
#
# Exit codes:
#   0 success
#   2 missing operator credentials (external dependency)
#   3 dump/restore failure

set -euo pipefail

HOST="${OTP_DBA_HOST:-${MYSQL_HOST:-127.0.0.1}}"
PORT="${OTP_DBA_PORT:-${MYSQL_PORT:-3306}}"
SRC_DB="${MYSQL_DB:-sentiment_demo}"
RESTORE_DB="${OTP_RESTORE_DB:-${SRC_DB}_restore_drill}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OTP_BACKUP_DIR:-/tmp/otp-backups}"
DUMP_FILE="${OUT_DIR}/${SRC_DB}-${STAMP}.sql"

if [[ -z "${OTP_DBA_USER:-}" || -z "${OTP_DBA_PASSWORD:-}" ]]; then
  cat <<EOF
EXTERNAL OPERATOR ACTION REQUIRED

Provide a DBA role distinct from the application runtime user, then re-run:

  export OTP_DBA_USER='otp_backup_operator'
  export OTP_DBA_PASSWORD='***'
  export OTP_DBA_HOST='${HOST}'
  export MYSQL_DB='${SRC_DB}'
  $0

Suggested grants (adjust host):

  CREATE USER 'otp_backup_operator'@'%' IDENTIFIED BY '***';
  GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER ON \`${SRC_DB}\`.* TO 'otp_backup_operator'@'%';
  GRANT CREATE, DROP, ALTER, INDEX, INSERT, UPDATE, DELETE, REFERENCES
    ON \`${RESTORE_DB}\`.* TO 'otp_backup_operator'@'%';
  -- Creating the restore database itself may also need:
  GRANT CREATE ON *.* TO 'otp_backup_operator'@'%';
  FLUSH PRIVILEGES;
EOF
  exit 2
fi

mkdir -p "${OUT_DIR}"
MYSQL=(mysql -h "${HOST}" -P "${PORT}" --protocol=TCP -u "${OTP_DBA_USER}" -p"${OTP_DBA_PASSWORD}")
DUMP=(mysqldump -h "${HOST}" -P "${PORT}" --protocol=TCP -u "${OTP_DBA_USER}" -p"${OTP_DBA_PASSWORD}"
  --single-transaction --routines --triggers --no-tablespaces)

echo "Dumping ${SRC_DB} -> ${DUMP_FILE}"
"${DUMP[@]}" "${SRC_DB}" > "${DUMP_FILE}"

echo "Recreating isolated restore database ${RESTORE_DB}"
"${MYSQL[@]}" -e "DROP DATABASE IF EXISTS \`${RESTORE_DB}\`; CREATE DATABASE \`${RESTORE_DB}\`;"
"${MYSQL[@]}" "${RESTORE_DB}" < "${DUMP_FILE}"

echo "Validating restore counts"
"${MYSQL[@]}" -N -e "
SELECT 'source_registry', COUNT(*) FROM \`${RESTORE_DB}\`.source_registry
UNION ALL SELECT 'civic_documents', COUNT(*) FROM \`${RESTORE_DB}\`.civic_documents
UNION ALL SELECT 'civic_document_chunks', COUNT(*) FROM \`${RESTORE_DB}\`.civic_document_chunks;
"

echo "Dropping restore database"
"${MYSQL[@]}" -e "DROP DATABASE IF EXISTS \`${RESTORE_DB}\`;"
echo "BACKUP_RESTORE_DRILL_OK file=${DUMP_FILE}"
