#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <commit-message> <pathspec> [pathspec...]" >&2
  exit 2
fi

message="$1"
shift
branch="${GITHUB_REF_NAME:-main}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$@"

if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

git commit -m "$message"

for attempt in 1 2 3; do
  if git push origin "HEAD:${branch}"; then
    exit 0
  fi
  echo "Push failed on attempt ${attempt}; rebasing onto origin/${branch} before retry." >&2
  git pull --rebase --autostash origin "$branch"
  sleep $((attempt * 3))
done

git push origin "HEAD:${branch}"
