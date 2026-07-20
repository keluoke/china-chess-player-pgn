#!/usr/bin/env bash
set -euo pipefail

# Commit the given pathspecs as the github-actions bot and push with rebase
# retries. Emits `committed=true|false` to $GITHUB_OUTPUT (when running under
# Actions) so callers can gate a follow-up deploy on whether anything changed.

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <commit-message> <pathspec> [pathspec...]" >&2
  exit 2
fi

message="$1"
shift
# PUSH_BRANCH overrides the target: workflows triggered by a push to another
# branch (e.g. ingest-local-data fires on local-data) still commit to main.
branch="${PUSH_BRANCH:-${GITHUB_REF_NAME:-main}}"

# A delete may already have landed on main during an ingest retry. Keep exact
# manifest scope, but omit pathspecs that are neither present nor tracked so
# `git add -A` treats such idempotent deletes as no-ops instead of fatal errors.
paths=()
for path in "$@"; do
  if [ -e "$path" ] || [ -L "$path" ] || git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    paths+=("$path")
  fi
done

set_committed() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "committed=$1" >> "$GITHUB_OUTPUT"
  fi
}

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
if [ "${CI_COMMIT_FORCE_ADD:-false}" = "true" ]; then
  # The ingest workflow passes only paths from a validated release manifest.
  # Some machine-data roots are intentionally ignored in a maintainer clone,
  # so those exact paths must be force-added on the cloud side as well.
  if [ "${#paths[@]}" -gt 0 ]; then
    git add --sparse -f -A -- "${paths[@]}"
  fi
else
  if [ "${#paths[@]}" -gt 0 ]; then
    git add --sparse -A -- "${paths[@]}"
  fi
fi

if git diff --cached --quiet; then
  echo "No changes to commit."
  set_committed false
  exit 0
fi

git commit -m "$message"

pushed=false
for attempt in 1 2 3; do
  if git push origin "HEAD:${branch}"; then
    pushed=true
    break
  fi
  echo "Push failed on attempt ${attempt}; rebasing onto origin/${branch} before retry." >&2
  # -X theirs: these are bot data commits; on overlap (e.g. regenerated
  # manifests) the version being committed wins over what raced onto main.
  git pull --rebase --autostash -X theirs origin "$branch" \
    || { git rebase --abort >/dev/null 2>&1 || true; }
  sleep $((attempt * 3))
done

if [ "$pushed" != "true" ]; then
  # Final attempt after one more rebase; if this fails, fail the job loudly
  # instead of silently reporting committed=true without a push.
  git pull --rebase --autostash -X theirs origin "$branch"
  git push origin "HEAD:${branch}"
fi

set_committed true
