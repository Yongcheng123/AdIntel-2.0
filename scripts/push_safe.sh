#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

usage() {
  cat <<'EOF'
Usage: scripts/push_safe.sh [options]

Safely push one commit to GitHub, Hugging Face, or both by:
1. fetching the remote branch
2. creating a temporary branch from the remote tip
3. cherry-picking the selected commit
4. pushing that temporary branch back to the remote branch

Options:
  --remote <origin|hf|both>   Which remote to push to. Default: both
  --branch <name>             Remote branch name. Default: main
  --commit <sha>              Commit to transplant. Default: HEAD
  --hf-token-env <name>       Env var to read Hugging Face token from. Default: HF_TOKEN
  -h, --help                  Show this help text

Examples:
  scripts/push_safe.sh
  scripts/push_safe.sh --remote origin --commit HEAD
  HF_TOKEN=hf_xxx scripts/push_safe.sh --remote hf
EOF
}

REMOTE_MODE="both"
TARGET_BRANCH="main"
TARGET_COMMIT="HEAD"
HF_TOKEN_ENV="HF_TOKEN"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote)
      REMOTE_MODE="${2:-}"
      shift 2
      ;;
    --branch)
      TARGET_BRANCH="${2:-}"
      shift 2
      ;;
    --commit)
      TARGET_COMMIT="${2:-}"
      shift 2
      ;;
    --hf-token-env)
      HF_TOKEN_ENV="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "${REMOTE_MODE}" in
  origin|hf|both)
    ;;
  *)
    echo "Invalid --remote value: ${REMOTE_MODE}" >&2
    exit 1
    ;;
esac

if ! git rev-parse --verify "${TARGET_COMMIT}^{commit}" >/dev/null 2>&1; then
  echo "Commit not found: ${TARGET_COMMIT}" >&2
  exit 1
fi

resolve_hf_url() {
  local remote_url token
  remote_url="$(git remote get-url hf)"
  token="${!HF_TOKEN_ENV:-}"

  if [[ -n "${token}" && "${remote_url}" == https://user:@huggingface.co/* ]]; then
    printf '%s\n' "${remote_url/https:\/\/user:@/https:\/\/user:${token}@}"
    return
  fi

  printf '%s\n' "${remote_url}"
}

push_one_remote() {
  local remote_name="$1"
  local fetch_ref="$2"
  local push_ref="$3"
  local remote_url_override="${4:-}"
  local temp_branch="push-${remote_name}-$(date +%s)"

  if git show-ref --verify --quiet "refs/heads/${temp_branch}"; then
    git branch -D "${temp_branch}" >/dev/null 2>&1 || true
  fi

  echo "Fetching ${remote_name}/${TARGET_BRANCH}..."
  if [[ -n "${remote_url_override}" ]]; then
    git -c "remote.${remote_name}.url=${remote_url_override}" fetch "${fetch_ref}" "${TARGET_BRANCH}"
  else
    git fetch "${fetch_ref}" "${TARGET_BRANCH}"
  fi

  git switch -c "${temp_branch}" FETCH_HEAD >/dev/null

  cleanup_branch() {
    git switch - >/dev/null 2>&1 || true
    git branch -D "${temp_branch}" >/dev/null 2>&1 || true
  }

  trap cleanup_branch RETURN

  echo "Cherry-picking ${TARGET_COMMIT} onto ${remote_name}/${TARGET_BRANCH}..."
  if git cherry-pick "${TARGET_COMMIT}" >/dev/null 2>&1; then
    :
  else
    if git rev-parse --verify CHERRY_PICK_HEAD >/dev/null 2>&1; then
      if git diff --quiet && git diff --cached --quiet; then
        git cherry-pick --skip >/dev/null 2>&1 || true
        echo "Nothing new to push for ${remote_name}; commit is already applied."
        return 0
      fi
      echo "Cherry-pick for ${remote_name} needs manual conflict resolution." >&2
      return 1
    fi
    echo "Cherry-pick failed for ${remote_name}." >&2
    return 1
  fi

  echo "Pushing ${temp_branch} -> ${remote_name}/${TARGET_BRANCH}..."
  if [[ -n "${remote_url_override}" ]]; then
    git -c "remote.${remote_name}.url=${remote_url_override}" push "${push_ref}" "HEAD:${TARGET_BRANCH}"
  else
    git push "${push_ref}" "HEAD:${TARGET_BRANCH}"
  fi

  local remote_head
  if [[ -n "${remote_url_override}" ]]; then
    remote_head="$(git ls-remote "${remote_url_override}" "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')"
  else
    remote_head="$(git ls-remote "${fetch_ref}" "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')"
  fi
  echo "${remote_name}/${TARGET_BRANCH} now at ${remote_head}"
}

if [[ "${REMOTE_MODE}" == "origin" || "${REMOTE_MODE}" == "both" ]]; then
  push_one_remote "origin" "origin" "origin"
fi

if [[ "${REMOTE_MODE}" == "hf" || "${REMOTE_MODE}" == "both" ]]; then
  HF_URL="$(resolve_hf_url)"
  push_one_remote "hf" "hf" "hf" "${HF_URL}"
fi

echo "Finished."
