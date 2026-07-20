#!/usr/bin/env bash
set -euo pipefail

# Keep code work separate from the append-only collector checkout. GitHub
# traffic is always given an explicit terminal proxy; source collection never
# calls this helper and therefore never inherits these variables.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CURRENT_ROLE="$(git -C "$CURRENT_ROOT" config --get chessdb.workspaceRole || true)"
if [ "$CURRENT_ROLE" = "code" ]; then
  SAVED_COLLECTOR_ROOT="$(git -C "$CURRENT_ROOT" config --get chessdb.collectorRoot || true)"
  COLLECTOR_ROOT="${COLLECTOR_WORKSPACE:-${SAVED_COLLECTOR_ROOT:-${CURRENT_ROOT%-code}}}"
  DEFAULT_CODE_ROOT="$CURRENT_ROOT"
else
  COLLECTOR_ROOT="$CURRENT_ROOT"
  DEFAULT_CODE_ROOT="$(dirname "$COLLECTOR_ROOT")/$(basename "$COLLECTOR_ROOT")-code"
fi
CODE_ROOT="${CODE_WORKSPACE:-${2:-$DEFAULT_CODE_ROOT}}"
GITHUB_PROXY_URL="${GITHUB_PROXY_URL:-http://127.0.0.1:15236}"

usage() {
  cat <<'EOF'
Usage: bash Scripts/local/code_workspace.sh <init|configure|sync|status|push> [path]

  init       Create a blobless sparse code workspace from origin/main.
  configure  Apply the code-workspace marker, sparse paths and sidebar proxy.
  sync       Fetch origin/main through the explicit proxy and fast-forward main.
  status     Show the role, branch, size and working-tree status without network.
  push       Push main through the explicit proxy.

Environment:
  CODE_WORKSPACE    Override the sibling target path.
  GITHUB_PROXY_URL  Override http://127.0.0.1:15236.
EOF
}

github_git() {
  HTTP_PROXY="$GITHUB_PROXY_URL" \
  HTTPS_PROXY="$GITHUB_PROXY_URL" \
  http_proxy="$GITHUB_PROXY_URL" \
  https_proxy="$GITHUB_PROXY_URL" \
    git "$@"
}

require_code_workspace() {
  if [ ! -d "$CODE_ROOT/.git" ]; then
    echo "code workspace not found: $CODE_ROOT" >&2
    echo "run: bash Scripts/local/code_workspace.sh init '$CODE_ROOT'" >&2
    exit 2
  fi
  if [ "$CODE_ROOT" = "$COLLECTOR_ROOT" ]; then
    echo "refusing to use the collector checkout as the code workspace" >&2
    exit 2
  fi
}

configure_workspace() {
  require_code_workspace
  git -C "$CODE_ROOT" config --local chessdb.workspaceRole code
  git -C "$CODE_ROOT" config --local chessdb.collectorRoot "$COLLECTOR_ROOT"
  # This repo-local setting is what lets GUI/sidebar Git use the terminal-only
  # proxy. Terminal Git still receives explicit variables via github_git.
  git -C "$CODE_ROOT" config --local http.proxy "$GITHUB_PROXY_URL"
  git -C "$CODE_ROOT" sparse-checkout init --no-cone
  git -C "$CODE_ROOT" sparse-checkout set --no-cone \
    '/*' \
    '!/data/generated/' \
    '!/docs/data/' \
    '!/docs/api/'
}

command="${1:-}"
case "$command" in
  init)
    if [ -e "$CODE_ROOT" ]; then
      echo "target already exists: $CODE_ROOT" >&2
      exit 2
    fi
    git -C "$COLLECTOR_ROOT" config --local chessdb.workspaceRole collector
    origin_url="$(git -C "$COLLECTOR_ROOT" remote get-url origin)"
    github_git clone --filter=blob:none --no-checkout --single-branch --branch main \
      "$origin_url" "$CODE_ROOT"
    configure_workspace
    github_git -C "$CODE_ROOT" checkout main
    ;;
  configure)
    configure_workspace
    ;;
  sync)
    require_code_workspace
    if [ -n "$(git -C "$CODE_ROOT" status --porcelain)" ]; then
      echo "code workspace is dirty; commit or stash before sync" >&2
      exit 3
    fi
    configure_workspace
    git -C "$CODE_ROOT" switch main
    github_git -C "$CODE_ROOT" fetch --prune origin main
    git -C "$CODE_ROOT" merge --ff-only origin/main
    ;;
  status)
    require_code_workspace
    printf 'workspace=%s\n' "$CODE_ROOT"
    printf 'role=%s\n' "$(git -C "$CODE_ROOT" config --get chessdb.workspaceRole || true)"
    printf 'branch=%s\n' "$(git -C "$CODE_ROOT" branch --show-current)"
    printf 'proxy=%s\n' "$(git -C "$CODE_ROOT" config --get http.proxy || true)"
    du -sh "$CODE_ROOT/.git" "$CODE_ROOT"
    git -C "$CODE_ROOT" status --short --branch
    ;;
  push)
    require_code_workspace
    if [ "$(git -C "$CODE_ROOT" branch --show-current)" != "main" ]; then
      echo "ordinary code publication must push main" >&2
      exit 3
    fi
    github_git -C "$CODE_ROOT" push origin main
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
