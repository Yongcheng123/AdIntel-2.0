#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Required environment variables — set these before running the script.
SERVER_DATABASE_URL="${SERVER_DATABASE_URL:?Set SERVER_DATABASE_URL (psql-compatible connection string)}"
ADINTEL_DATABASE_URL="${ADINTEL_DATABASE_URL:?Set ADINTEL_DATABASE_URL (SQLAlchemy connection string)}"
ADVERTISER_NAME="${ADVERTISER_NAME:-Chime}"
RUN_ALL_FROM_CONFIG="${RUN_ALL_FROM_CONFIG:-false}"
PLATFORM="${PLATFORM:-sensortower}"
COUNTRIES="${COUNTRIES:-}"
HEADLESS="${HEADLESS:-true}"
DEBUG="${DEBUG:-false}"
VERBOSE="${VERBOSE:-true}"
USE_CDP="${USE_CDP:-false}"
DRY_RUN="${DRY_RUN:-false}"
SYNC_CATALOG="${SYNC_CATALOG:-true}"
RUN_COLLECTION="${RUN_COLLECTION:-true}"

export SERVER_DATABASE_URL
export ADINTEL_DATABASE_URL
export ADINTEL_AUTO_APPLY_SCHEMA=false

cd "${ROOT_DIR}"

echo "Using server database: $(echo "${SERVER_DATABASE_URL}" | sed 's|://[^@]*@|://***:***@|')"
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
  successes=()
  failures=()
  if [[ "${RUN_ALL_FROM_CONFIG}" == "true" ]]; then
    while IFS= read -r advertiser; do
      advertisers+=("${advertiser}")
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
    if [[ "${USE_CDP}" == "true" ]]; then
      cmd+=(--use-cdp)
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
      echo "  [dry-run] ${cmd[*]}"
      continue
    fi

    if "${cmd[@]}"; then
      successes+=("${advertiser}")
    else
      echo "Collection failed for ${advertiser}. Continuing..." >&2
      failures+=("${advertiser}")
    fi
  done

  echo
  echo "Collection summary:"
  echo "  successful: ${#successes[@]}"
  if [[ ${#successes[@]} -gt 0 ]]; then
    printf '  success list: %s\n' "${successes[*]}"
  fi
  echo "  failed: ${#failures[@]}"
  if [[ ${#failures[@]} -gt 0 ]]; then
    printf '  failure list: %s\n' "${failures[*]}"
  fi
fi

echo
echo "Latest scrape runs:"
psql "${SERVER_DATABASE_URL}" -c \
  "select id, advertiser_name, platform, status, message, metadata from scrape_runs order by id desc limit 5;"
