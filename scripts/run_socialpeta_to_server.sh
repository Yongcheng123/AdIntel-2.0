#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEFAULT_ADINTEL_DATABASE_URL="$(
  cd "${ROOT_DIR}"
  ./.venv/bin/python - <<'PY'
from adintel.core.settings import get_settings

print(get_settings().database_url)
PY
)"

ADINTEL_DATABASE_URL="${ADINTEL_DATABASE_URL:-${DEFAULT_ADINTEL_DATABASE_URL}}"
ADVERTISER_NAME="${ADVERTISER_NAME:-}"
RUN_ALL_FROM_CONFIG="${RUN_ALL_FROM_CONFIG:-true}"
PAGES="${PAGES:-3}"
HEADLESS="${HEADLESS:-false}"
VERBOSE="${VERBOSE:-true}"
MODE="${MODE:-missing}"
SYNC_CATALOG="${SYNC_CATALOG:-true}"
RUN_COLLECTION="${RUN_COLLECTION:-true}"
GROUP_CONFIG_FILE="${GROUP_CONFIG_FILE:-config/socialpeta_groups.yaml}"
REPORT_OUTPUT_FILE="${REPORT_OUTPUT_FILE:-}"

export ADINTEL_DATABASE_URL
export ADINTEL_AUTO_APPLY_SCHEMA=true

cd "${ROOT_DIR}"

RUN_LOCK_DIR="${ROOT_DIR}/state/.run_socialpeta_to_server.lock"
mkdir -p "${ROOT_DIR}/state"
if ! mkdir "${RUN_LOCK_DIR}" 2>/dev/null; then
  echo "Another SocialPeta run appears to be in progress (lock: ${RUN_LOCK_DIR})."
  exit 1
fi
trap 'rmdir "${RUN_LOCK_DIR}" 2>/dev/null || true' EXIT

echo "Using database: ${ADINTEL_DATABASE_URL}"
echo "Initializing schema..."
./.venv/bin/adintel init-db

if [[ "${SYNC_CATALOG}" == "true" ]]; then
  echo
  echo "Syncing advertiser catalog..."
  ./.venv/bin/adintel advertisers sync-catalog
fi

if [[ "${RUN_COLLECTION}" == "true" ]]; then
  echo
  echo "Running SocialPeta collection..."
  cmd=(./.venv/bin/python scripts/socialpeta_collect_batch.py --group-config-file "${GROUP_CONFIG_FILE}" --pages "${PAGES}" --mode "${MODE}")
  if [[ -n "${REPORT_OUTPUT_FILE}" ]]; then
    cmd+=(--report-output-file "${REPORT_OUTPUT_FILE}")
  fi

  if [[ "${RUN_ALL_FROM_CONFIG}" != "true" && -n "${ADVERTISER_NAME}" ]]; then
    cmd+=(--advertiser-name "${ADVERTISER_NAME}")
  fi
  if [[ "${HEADLESS}" == "true" ]]; then
    cmd+=(--headless)
  fi
  if [[ "${VERBOSE}" == "true" ]]; then
    cmd+=(--verbose)
  fi

  "${cmd[@]}"
fi

echo
echo "Latest SocialPeta scrape runs:"
./.venv/bin/python - <<'PY'
from sqlalchemy import create_engine, text
from adintel.core.settings import get_settings

engine = create_engine(get_settings().database_url)
with engine.connect() as conn:
    rows = conn.execute(text("""
        select id, advertiser_name, platform, status, message
        from scrape_runs
        where platform = 'socialpeta'
        order by id desc
        limit 10
    """)).fetchall()
    for row in rows:
        print(tuple(row))
PY
