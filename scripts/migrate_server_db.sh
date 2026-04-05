#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA_FILE="${ROOT_DIR}/sql/schema.sql"

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
psql "${SERVER_DATABASE_URL}" -f "${SCHEMA_FILE}"

echo
echo "Verifying scrape_runs table..."
psql "${SERVER_DATABASE_URL}" -c '\d scrape_runs'
