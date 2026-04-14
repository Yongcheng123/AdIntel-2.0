#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
export PYTHONUNBUFFERED=1

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing virtualenv python at ${PYTHON_BIN}" >&2
  exit 1
fi

# Defaults can be overridden via env vars.
CONFIG_FILE="${CONFIG_FILE:-${ROOT_DIR}/config/otterly_batch.yaml}"
PAGE_SIZE="${PAGE_SIZE:-100}"
WRITE_DB="${WRITE_DB:-true}"
# Keep the batch database-only unless explicitly overridden.
SAVE_FILES="${SAVE_FILES:-false}"
SHOW_DB_SUMMARY="${SHOW_DB_SUMMARY:-true}"

# Resolve the app database URL via AdIntel settings (.env-aware) unless explicitly overridden.
DEFAULT_ADINTEL_DATABASE_URL="$(
  cd "${ROOT_DIR}"
  "${PYTHON_BIN}" - <<'PY'
from adintel.core.settings import get_settings

print(get_settings().database_url)
PY
)"

ADINTEL_DATABASE_URL="${ADINTEL_DATABASE_URL:-${DEFAULT_ADINTEL_DATABASE_URL}}"
PSQL_DATABASE_URL="${PSQL_DATABASE_URL:-${ADINTEL_DATABASE_URL/postgresql+psycopg/postgresql}}"

cd "${ROOT_DIR}"

RUN_LOCK_DIR="${ROOT_DIR}/state/.run_otterly_batch.lock"
mkdir -p "${ROOT_DIR}/state"
if ! mkdir "${RUN_LOCK_DIR}" 2>/dev/null; then
  echo "Another Otterly batch run appears to be in progress (lock: ${RUN_LOCK_DIR})."
  echo "Wait for the running job to finish, or remove the stale lock directory if no process is active."
  exit 1
fi
trap 'rmdir "${RUN_LOCK_DIR}" 2>/dev/null || true' EXIT

echo "Using config: ${CONFIG_FILE}"
echo "Database writes: ${WRITE_DB}"
echo "Save files: ${SAVE_FILES}"
echo "Page size: ${PAGE_SIZE}"

cmd=(
  "${PYTHON_BIN}"
  scripts/otterly_brand_reports_api.py
  batch-collect
  --config-file "${CONFIG_FILE}"
  --page-size "${PAGE_SIZE}"
)

if [[ "${WRITE_DB}" == "true" ]]; then
  cmd+=(--write-db)
else
  cmd+=(--no-write-db)
fi

if [[ "${SAVE_FILES}" == "true" ]]; then
  cmd+=(--save-files)
else
  cmd+=(--no-save-files)
fi

echo
echo "Running Otterly batch collection..."
"${cmd[@]}"

if [[ "${WRITE_DB}" == "true" && "${SHOW_DB_SUMMARY}" == "true" ]]; then
  if command -v psql >/dev/null 2>&1 && [[ "${PSQL_DATABASE_URL}" == postgresql* ]]; then
    echo
    echo "Latest Otterly prompt rows:"
    psql "${PSQL_DATABASE_URL}" -P pager=off -c \
      "select id, target_brand_or_domain_name, country_code, ai_engine, query_window_end_date from otterlyai_prompts order by id desc limit 5;"

    echo
    echo "Latest Otterly citation rows:"
    psql "${PSQL_DATABASE_URL}" -P pager=off -c \
      "select id, target_brand_or_domain_name, country_code, ai_engine, query_window_end_date from otterlyai_citations order by id desc limit 5;"
  else
    echo
    echo "Skipping DB summary: psql is unavailable or the configured database is not PostgreSQL."
  fi
fi
