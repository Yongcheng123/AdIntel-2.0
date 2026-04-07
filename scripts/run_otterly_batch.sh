#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
CONFIG_FILE="${1:-${ROOT_DIR}/config/otterly_batch.yaml}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing virtualenv python at ${PYTHON_BIN}" >&2
  exit 1
fi

cd "${ROOT_DIR}"

exec "${PYTHON_BIN}" scripts/otterly_brand_reports_api.py batch-collect \
  --config-file "${CONFIG_FILE}" \
  --write-db \
  --save-files
