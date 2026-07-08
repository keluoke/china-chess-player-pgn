#!/usr/bin/env bash
#
# Local scraping entrypoint. Run this on your own machine (residential IP) where
# chess-results.com and ratings.fide.com are reachable — GitHub's datacenter IPs
# are blocked, which is why the scraping half of the pipeline lives here instead
# of in Actions.
#
# What it does:
#   1. Runs the network-dependent scraper(s) for the chosen command.
#   2. Commits the RAW scraped data and pushes to main (as you, not the bot).
#   3. That push triggers `rebuild-indexes.yml` on GitHub, which rebuilds every
#      derived index (no network) and deploys the static site.
#
# So: scraping = local; indexing + deploy = GitHub Actions.
#
# Usage:
#   Scripts/local/refresh.sh <command> [--no-push] [-- <extra args>]
#
# Commands:
#   registry     Download the FIDE rating list and rebuild the CHN registry.
#   crawl        Crawl Chess-Results player events (incremental) + fetch PGN.
#   events       Scrape event names + registry + full-event PGN (li-chengzhi).
#   aliases      Scrape Chinese names from Chess-Results and apply to registry.
#   promote      Promote publicly distributable Chess-Results PGN.
#   reconcile    Probe Chess-Results coverage gaps, promote + fetch missing PGN.
#   bulk         Mirror Lichess broadcast shards + rebuild youth packs.
#   pgn          Fetch missing per-tournament PGN from Chess-Results.
#   all          Routine incremental refresh: registry, then crawl.
#   reindex      LOCAL pure rebuild (indexes/registry aliases/domestic). Optional;
#                normally Actions does this. No network.
#
# Options:
#   --no-push    Commit locally but do not push (skips the Actions handoff).
#   -- <args>    Everything after `--` is passed through to the underlying script.
#
# NOTE: no `set -u`. macOS ships bash 3.2, where `set -u` + expanding an empty
# array (${EXTRA[@]+"${EXTRA[@]}"}) aborts with "unbound variable". We keep -e and pipefail
# and use the `${arr[@]+"${arr[@]}"}` idiom so empty arrays are always safe.
set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

command="${1:-}"
shift || true

PUSH=true
EXTRA=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-push) PUSH=false; shift ;;
    --) shift; EXTRA=("$@"); break ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done

py() { python3 "$@"; }

# --- progress / notification helpers ---------------------------------------
BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "${BOLD}${CYAN}" "$*" "$RESET"; }

notify_mac() {  # notify_mac <message> — macOS notification, no-op elsewhere
  command -v osascript >/dev/null 2>&1 || return 0
  osascript -e "display notification \"$1\" with title \"棋手数据刷新\" sound name \"Glass\"" >/dev/null 2>&1 || true
}

# Repo web URL for the final hint, with any embedded credential scrubbed.
repo_web_url() {
  git remote get-url origin 2>/dev/null \
    | sed -E 's#https://[^@/]*@#https://#; s#\.git$##'
}

PUSH_SUMMARY=""
on_exit() {
  status=$?
  # Only report for real commands (not help/usage paths).
  [ -n "${REPORT_ON_EXIT:-}" ] || return 0
  if [ "$status" -eq 0 ]; then
    printf '\n%s✅ 完成:%s%s\n' "$GREEN" "$command  $PUSH_SUMMARY" "$RESET"
    notify_mac "完成:$command ✅ $PUSH_SUMMARY"
  else
    printf '\n%s❌ 失败(退出码 %s):%s — 请查看上方输出定位错误%s\n' "$RED" "$status" "$command" "$RESET"
    notify_mac "失败:$command(码 $status)❌ 详见终端输出"
  fi
}
trap on_exit EXIT

# Sync with origin BEFORE scraping. Every local push makes Actions commit a
# "Rebuild derived indexes" bot commit, so the local clone is always behind by
# the next run; without this the final `git push` would be rejected.
sync_with_remote() {
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  step "[1/3] 同步远端(git pull --rebase)"
  if git fetch origin "$branch" 2>/dev/null; then
    git pull --rebase --autostash origin "$branch" || {
      echo "WARNING: rebase onto origin/$branch failed; resolve conflicts and re-run." >&2
      exit 1
    }
  else
    echo "WARNING: cannot reach origin (offline?); continuing without sync." >&2
  fi
}

commit_and_push() {
  local message="$1"; shift
  step "[3/3] 提交并推送"
  git add "$@"
  if git diff --cached --quiet; then
    echo "No changes to commit."
    PUSH_SUMMARY="(无新数据,未推送)"
    return 0
  fi
  local changed
  changed="$(git diff --cached --stat | tail -1)"
  git commit -m "$message"
  if [ "$PUSH" = "true" ]; then
    local branch pushed=false
    branch="$(git rev-parse --abbrev-ref HEAD)"
    # The rebuild bot may have pushed while we were scraping; rebase and retry.
    for attempt in 1 2 3; do
      if git push origin "$branch"; then pushed=true; break; fi
      echo "Push rejected (attempt $attempt); rebasing onto origin/$branch and retrying." >&2
      git pull --rebase --autostash origin "$branch"
      sleep $((attempt * 2))
    done
    if [ "$pushed" != "true" ]; then
      echo "ERROR: push still failing after 3 attempts; run 'git push' manually." >&2
      exit 1
    fi
    PUSH_SUMMARY="已推送($changed)"
    echo "Pushed. GitHub Actions 将重建索引并部署到 GitHub Pages + Cloudflare(约 3-5 分钟)。"
    echo "查看进度:$(repo_web_url)/actions"
  else
    PUSH_SUMMARY="已提交,未推送(--no-push)"
    echo "Committed locally (--no-push). Run 'git push' to trigger index rebuild + deploy."
  fi
}

# Pull latest (incl. bot rebuild commits) before scraping/committing.
case "$command" in
  ""|-h|--help|help) : ;;
  *)
    REPORT_ON_EXIT=1
    sync_with_remote
    step "[2/3] 抓取:$command(数据写入仓库内 docs/data/ 与 data/manual/)"
    ;;
esac

case "$command" in
  registry)
    py Scripts/sync_chinese_players.py ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Update Chinese player registry (local)" docs/data/registry
    ;;

  crawl)
    py Scripts/crawl_player_events.py --delay 1.0 --workers 2 --fetch-games ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Crawl Chess-Results player events (local)" data/manual docs/data
    ;;

  events)
    py Scripts/sync_chess_results_starting_rank_aliases.py --delay 1.0
    py Scripts/sync_chinese_players.py || py Scripts/apply_aliases_to_registry.py
    py Scripts/fetch_event_pgn.py --workers 3 --category li-chengzhi ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Ingest event archive names and PGN (local)" data/manual docs/data
    ;;

  aliases)
    py Scripts/sync_chess_results_starting_rank_aliases.py ${EXTRA[@]+"${EXTRA[@]}"}
    py Scripts/sync_chinese_players.py || py Scripts/apply_aliases_to_registry.py
    commit_and_push "Update Chinese name aliases (local)" data/manual docs/data/registry
    ;;

  promote)
    py Scripts/promote_public_pgn.py --scan-chess-results --max-players 25 --max-games 0 --skip-index ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Promote public PGN archive (local)" docs/data
    ;;

  reconcile)
    py Scripts/reconcile_pgn_sources.py --write-audit --discover-chess-results \
      --max-players 120 --max-known-missing 80 --max-neighbor-ids 80 --delay 1.2 ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Reconcile PGN source coverage (local)" docs/data
    ;;

  bulk)
    py Scripts/sync_lichess_broadcast_bulk.py --metadata-only --mirror --index-youth ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Update Lichess broadcast bulk archive (local)" docs/data
    ;;

  pgn)
    py Scripts/sync_static_pgn.py --fetch-missing --max-downloads 50 ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Update static PGN archive (local)" docs/data
    ;;

  all)
    py Scripts/sync_chinese_players.py
    py Scripts/crawl_player_events.py --delay 1.0 --workers 2 --fetch-games
    commit_and_push "Routine local refresh (registry + crawl)" data/manual docs/data
    ;;

  reindex)
    # Pure, no network — mirrors what rebuild-indexes.yml does on Actions.
    [ -f docs/data/registry/players.json ] && py Scripts/apply_aliases_to_registry.py || true
    py Scripts/sync_domestic_players.py
    py Scripts/sync_static_pgn.py
    py Scripts/build_static_player_pgn.py
    commit_and_push "Rebuild derived indexes (local)" docs/data
    ;;

  ""|-h|--help|help)
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;

  *)
    echo "Unknown command: $command" >&2
    echo "Run 'Scripts/local/refresh.sh help' for usage." >&2
    exit 2
    ;;
esac
