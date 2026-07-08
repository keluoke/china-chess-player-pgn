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
branch="${GITHUB_REF_NAME:-main}"

set_committed() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "committed=$1" >> "$GITHUB_OUTPUT"
  fi
}

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$@"

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
  git pull --rebase --autostash origin "$branch"
  sleep $((attempt * 3))
done

if [ "$pushed" != "true" ]; then
  # Final attempt after one more rebase; if this fails, fail the job loudly
  # instead of silently reporting committed=true without a push.
  git pull --rebase --autostash origin "$branch"
  git push origin "HEAD:${branch}"
fi

set_committed true
