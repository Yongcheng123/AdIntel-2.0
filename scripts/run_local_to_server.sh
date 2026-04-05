#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve the app database URL via AdIntel settings (.env-aware) unless explicitly overridden.
DEFAULT_ADINTEL_DATABASE_URL="$(
  cd "${ROOT_DIR}"
  ./.venv/bin/python - <<'PY'
from adintel.core.settings import get_settings

print(get_settings().database_url)
PY
)"

# Edit these values before running the script.
ADINTEL_DATABASE_URL="${ADINTEL_DATABASE_URL:-${DEFAULT_ADINTEL_DATABASE_URL}}"
SERVER_DATABASE_URL="${SERVER_DATABASE_URL:-${ADINTEL_DATABASE_URL/postgresql+psycopg/postgresql}}"
ADVERTISER_NAME="${ADVERTISER_NAME:-}"
RUN_ALL_FROM_CONFIG="${RUN_ALL_FROM_CONFIG:-true}"
PLATFORM="${PLATFORM:-sensortower}"
COUNTRIES="${COUNTRIES:-}"
HEADLESS="${HEADLESS:-true}"
DEBUG="${DEBUG:-false}"
VERBOSE="${VERBOSE:-true}"
SYNC_CATALOG="${SYNC_CATALOG:-true}"
VALIDATE_CATALOG_DB="${VALIDATE_CATALOG_DB:-true}"
RUN_COLLECTION="${RUN_COLLECTION:-true}"

export SERVER_DATABASE_URL
export ADINTEL_DATABASE_URL
export ADINTEL_AUTO_APPLY_SCHEMA=false

cd "${ROOT_DIR}"

# Prevent concurrent runs from sharing the same browser profile state.
RUN_LOCK_DIR="${ROOT_DIR}/state/.run_local_to_server.lock"
mkdir -p "${ROOT_DIR}/state"
if ! mkdir "${RUN_LOCK_DIR}" 2>/dev/null; then
  echo "Another collection run appears to be in progress (lock: ${RUN_LOCK_DIR})."
  echo "Wait for the running job to finish, or remove the stale lock directory if no process is active."
  exit 1
fi
trap 'rmdir "${RUN_LOCK_DIR}" 2>/dev/null || true' EXIT

echo "Using server database: ${SERVER_DATABASE_URL}"
echo "Applying schema..."
bash "${ROOT_DIR}/scripts/migrate_server_db.sh"

if [[ "${SYNC_CATALOG}" == "true" ]]; then
  echo
  echo "Catalog sync is enabled (SYNC_CATALOG=true)."
  echo "This keeps DB runtime identifiers aligned with config/advertisers.yaml before collection."
  echo "Set SYNC_CATALOG=false only if you intentionally manage advertiser IDs directly in DB."
  echo "Syncing advertiser catalog..."
  ./.venv/bin/adintel advertisers sync-catalog
fi

if [[ "${VALIDATE_CATALOG_DB}" == "true" ]]; then
  echo
  echo "Validating catalog and DB alignment..."
  bash "${ROOT_DIR}/scripts/validate_catalog_vs_db.sh"
fi

if [[ "${RUN_COLLECTION}" == "true" ]]; then
  echo
  echo "Running collection..."
  advertisers=()
  if [[ "${RUN_ALL_FROM_CONFIG}" == "true" ]]; then
    while IFS= read -r advertiser; do
      [[ -n "${advertiser}" ]] && advertisers+=("${advertiser}")
    done < <(
      ./.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

config = Path("config/advertisers.yaml")
data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
for advertiser in data.get("advertisers", []):
    name = advertiser.get("name")
    if name:
        print(name)
PY
    )
  else
    advertisers=("${ADVERTISER_NAME}")
  fi

  for advertiser in "${advertisers[@]}"; do
    echo
    echo "Collecting: ${advertiser}"
    cmd=(./.venv/bin/adintel collect advertiser "${advertiser}" --platform "${PLATFORM}")

    if [[ -n "${COUNTRIES}" ]]; then
      cmd+=(--countries "${COUNTRIES}")
    fi
    if [[ "${HEADLESS}" == "true" ]]; then
      cmd+=(--headless)
    fi
    if [[ "${DEBUG}" == "true" ]]; then
      cmd+=(--debug)
    fi
    if [[ "${VERBOSE}" == "true" ]]; then
      cmd+=(--verbose)
    fi

    "${cmd[@]}"
  done
fi

echo
echo "Latest scrape runs:"
psql "${SERVER_DATABASE_URL}" -P pager=off -c \
  "select id, advertiser_name, platform, status, message, metadata from scrape_runs order by id desc limit 5;"
