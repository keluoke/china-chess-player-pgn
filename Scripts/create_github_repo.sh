#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="${1:-china-chess-player-pgn}"
VISIBILITY="${2:-private}"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is not installed."
  echo "Install it or create a repository on github.com, then run:"
  echo "  git remote add origin git@github.com:<owner>/$REPO_NAME.git"
  echo "  git push -u origin main"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is installed but not authenticated. Run: gh auth login"
  exit 1
fi

if [ ! -d .git ]; then
  git init
  git branch -M main
fi

git add .
git commit -m "Initial macOS China chess PGN app" || true
gh repo create "$REPO_NAME" "--$VISIBILITY" --source=. --remote=origin --push
