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
RUN_COLLECTION="${RUN_COLLECTION:-true}"

export SERVER_DATABASE_URL
export ADINTEL_DATABASE_URL
export ADINTEL_AUTO_APPLY_SCHEMA=false

cd "${ROOT_DIR}"

echo "Using server database: ${SERVER_DATABASE_URL}"
echo "Applying schema..."
bash "${ROOT_DIR}/scripts/migrate_server_db.sh"

if [[ "${SYNC_CATALOG}" == "true" ]]; then
  echo
  echo "Syncing advertiser catalog..."
  ./.venv/bin/adintel advertisers sync-catalog
fi

if [[ "${RUN_COLLECTION}" == "true" ]]; then
  echo
  echo "Running collection..."
  advertisers=()
  if [[ "${RUN_ALL_FROM_CONFIG}" == "true" ]]; then
    mapfile -t advertisers < <(
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
psql "${SERVER_DATABASE_URL}" -c \
  "select id, advertiser_name, platform, status, message, metadata from scrape_runs order by id desc limit 5;"
