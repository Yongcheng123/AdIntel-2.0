#!/usr/bin/env bash
# Collect AppFollow review data and store it in the server database.
#
# Reads config/appfollow_groups.yaml for app groups and item IDs.
# Requires a prior login: adintel login appfollow
#
# Key environment variables (all optional with sensible defaults):
#   ADINTEL_DATABASE_URL    — local app DB URL (resolved from .env by default)
#   SERVER_DATABASE_URL     — production DB to apply schema to (defaults to ADINTEL_DATABASE_URL)
#   HEADLESS                — "true" (default) or "false" to show browser
#   TEST                    — "true" to collect only the first advertiser group (default: false)
#   MODE                    — "missing" (default) or "all" to refresh all configured advertisers
#
# Usage:
#   bash scripts/run_appfollow_to_server.sh
#   TEST=true bash scripts/run_appfollow_to_server.sh           # Test first group only
#   HEADLESS=false bash scripts/run_appfollow_to_server.sh      # Show browser window

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve the app database URL via AdIntel settings (.env-aware) unless overridden.
DEFAULT_ADINTEL_DATABASE_URL="$(
  cd "${ROOT_DIR}"
  ./.venv/bin/python - <<'PY'
from adintel.core.settings import get_settings
print(get_settings().database_url)
PY
)"

ADINTEL_DATABASE_URL="${ADINTEL_DATABASE_URL:-${DEFAULT_ADINTEL_DATABASE_URL}}"
SERVER_DATABASE_URL="${SERVER_DATABASE_URL:-${ADINTEL_DATABASE_URL/postgresql+psycopg/postgresql}}"
HEADLESS="${HEADLESS:-true}"
TEST="${TEST:-false}"
MODE="${MODE:-missing}"

export SERVER_DATABASE_URL
export ADINTEL_DATABASE_URL
export ADINTEL_AUTO_APPLY_SCHEMA=false

cd "${ROOT_DIR}"

# Prevent concurrent runs from sharing the same browser profile state.
RUN_LOCK_DIR="${ROOT_DIR}/state/.run_appfollow_to_server.lock"
mkdir -p "${ROOT_DIR}/state"
if ! mkdir "${RUN_LOCK_DIR}" 2>/dev/null; then
  echo "Another AppFollow collection run appears to be in progress (lock: ${RUN_LOCK_DIR})."
  echo "Wait for the running job to finish, or remove the stale lock directory if no process is active."
  exit 1
fi
trap 'rmdir "${RUN_LOCK_DIR}" 2>/dev/null || true' EXIT

echo "Using server database: ${SERVER_DATABASE_URL}"
echo "Applying schema..."
bash "${ROOT_DIR}/scripts/migrate_server_db.sh"

echo
echo "Running AppFollow review collection..."

cmd=(./.venv/bin/python scripts/appfollow_run_all.py)
cmd+=(--mode "${MODE}")

if [[ "${HEADLESS}" == "true" ]]; then
  cmd+=(--headless)
fi
if [[ "${TEST}" == "true" ]]; then
  cmd+=(--test)
fi

"${cmd[@]}"

echo
echo "Latest AppFollow scrape runs:"
psql "${SERVER_DATABASE_URL}" -P pager=off -c \
  "SELECT id, advertiser_name, platform, status, message, started_at
   FROM scrape_runs
   WHERE platform = 'appfollow'
   ORDER BY id DESC
   LIMIT 10;" || true

echo
echo "AppFollow review row counts by advertiser:"
psql "${SERVER_DATABASE_URL}" -P pager=off -c \
  "SELECT advertiser_name, count(*) AS reviews, min(review_date) AS earliest, max(review_date) AS latest,
          round(avg(star_rating)::numeric, 2) AS avg_rating,
          count(*) FILTER (WHERE sentiment = 'positive') AS positive,
          count(*) FILTER (WHERE sentiment = 'negative') AS negative
   FROM appfollow_reviews
   GROUP BY advertiser_name
   ORDER BY advertiser_name;" || true

echo
echo "✅  Collection complete!"
