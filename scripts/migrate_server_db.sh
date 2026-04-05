#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA_FILE="${ROOT_DIR}/sql/schema.sql"
MIGRATIONS_DIR="${ROOT_DIR}/sql/migrations"

if [[ ! -f "${SCHEMA_FILE}" ]]; then
  echo "Schema file not found: ${SCHEMA_FILE}" >&2
  exit 1
fi

if [[ -z "${SERVER_DATABASE_URL:-}" ]]; then
  cat >&2 <<'EOF'
SERVER_DATABASE_URL is not set.

Example for Neon:
  export SERVER_DATABASE_URL='postgresql://neondb_owner:npg_F3gB1CftVasl@ep-little-cherry-anuymqi3.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require'
EOF
  exit 1
fi

echo "Applying schema: ${SCHEMA_FILE}"
psql "${SERVER_DATABASE_URL}" -v ON_ERROR_STOP=1 -f "${SCHEMA_FILE}"

echo
echo "Preparing migration state table..."
psql "${SERVER_DATABASE_URL}" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS adintel_migration_state (
  filename TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SQL

if [[ -d "${MIGRATIONS_DIR}" ]]; then
  while IFS= read -r migration_file; do
    migration_name="$(basename "${migration_file}")"
    already_applied="$(psql "${SERVER_DATABASE_URL}" -Atqc "SELECT 1 FROM adintel_migration_state WHERE filename = '${migration_name}' LIMIT 1")"
    if [[ "${already_applied}" == "1" ]]; then
      echo "Skipping already-applied migration: ${migration_name}"
      continue
    fi

    echo "Applying migration: ${migration_name}"
    psql "${SERVER_DATABASE_URL}" -v ON_ERROR_STOP=1 -f "${migration_file}"
    psql "${SERVER_DATABASE_URL}" -v ON_ERROR_STOP=1 -c "INSERT INTO adintel_migration_state (filename) VALUES ('${migration_name}')"
  done < <(find "${MIGRATIONS_DIR}" -maxdepth 1 -type f -name '*.sql' | sort)
fi

echo
echo "Verifying scrape_runs table..."
psql "${SERVER_DATABASE_URL}" -c '\d scrape_runs'
