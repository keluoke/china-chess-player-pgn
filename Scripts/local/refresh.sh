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
#   push         Re-push commits left behind by an earlier failed push.
#   reindex      LOCAL pure rebuild (indexes/registry aliases/domestic). Optional;
#                normally Actions does this. No network.
#
# GitHub connectivity: scrapers always go DIRECT (residential IP required);
# git fetch/push auto-detects a working route — direct first, then the local
# proxy ports used by Clash/V2Ray etc. Override with GITHUB_PROXY=http://...
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
DATA_COMMITTED=false
on_exit() {
  status=$?
  # Only report for real commands (not help/usage paths).
  [ -n "${REPORT_ON_EXIT:-}" ] || return 0
  if [ "$status" -eq 0 ]; then
    printf '\n%s✅ 完成:%s%s\n' "$GREEN" "$command  $PUSH_SUMMARY" "$RESET"
    notify_mac "完成:$command ✅ $PUSH_SUMMARY"
  elif [ "$DATA_COMMITTED" = "true" ]; then
    printf '\n%s⚠️  抓取成功、数据已提交,但推送失败:%s — 稍后选「push」重推即可%s\n' "$RED" "$command" "$RESET"
    notify_mac "数据已保存 ⚠️ 推送失败,稍后选 push 重推"
  else
    printf '\n%s❌ 失败(退出码 %s):%s — 请查看上方输出定位错误%s\n' "$RED" "$status" "$command" "$RESET"
    notify_mac "失败:$command(码 $status)❌ 详见终端输出"
  fi
}
trap on_exit EXIT

# --- GitHub connectivity ----------------------------------------------------
# The scrapers (chess-results.com / ratings.fide.com) MUST go direct — they
# block datacenter IPs, and proxy exits are datacenter IPs. But github.com is
# often unreachable directly from mainland China, while the user's browser
# works fine via a local proxy (Clash / V2Ray / ...). Git does not read the
# macOS system proxy, so we probe for a working route ourselves and pass it
# ONLY to git remote operations, never to the scrapers.
GIT_PROXY=""
GIT_PROXY_PROBED=false

github_ok() {  # github_ok [proxy] — can we complete an HTTPS request?
  if [ -n "$1" ]; then
    curl -s --max-time 6 -o /dev/null -x "$1" "https://github.com/"
  else
    curl -s --max-time 6 -o /dev/null "https://github.com/"
  fi
}

detect_git_proxy() {
  [ "$GIT_PROXY_PROBED" = "true" ] && return 0
  GIT_PROXY_PROBED=true
  if [ -n "${GITHUB_PROXY:-}" ]; then          # explicit override wins
    GIT_PROXY="$GITHUB_PROXY"
    echo "git 走 GITHUB_PROXY 环境变量指定的代理:$GIT_PROXY"
    return 0
  fi
  if github_ok ""; then
    return 0                                    # direct works, no proxy
  fi
  local p
  for p in "${https_proxy:-}" "${HTTPS_PROXY:-}" \
           http://127.0.0.1:7890 http://127.0.0.1:1087 \
           socks5h://127.0.0.1:1080 http://127.0.0.1:8118; do
    [ -n "$p" ] || continue
    if github_ok "$p"; then
      GIT_PROXY="$p"
      echo "github.com 直连失败,git 改走本地代理:$p(抓取仍直连)"
      return 0
    fi
  done
  echo "WARNING: github.com 直连和常见本地代理(7890/1087/1080/8118)均不可达。" >&2
  echo "         可开启代理软件后重试,或用 GITHUB_PROXY=http://127.0.0.1:端口 指定。" >&2
  return 1
}

xgit() {  # git remote operations, routed through the detected proxy if any
  if [ -n "$GIT_PROXY" ]; then
    git -c http.proxy="$GIT_PROXY" -c https.proxy="$GIT_PROXY" "$@"
  else
    git "$@"
  fi
}

# Sync with origin BEFORE scraping. Every local push makes Actions commit a
# "Rebuild derived indexes" bot commit, so the local clone is always behind by
# the next run; without this the final `git push` would be rejected. Failure
# here never blocks scraping — data is committed locally regardless.
sync_with_remote() {
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  step "[1/3] 同步远端(git pull --rebase)"
  detect_git_proxy || true
  # -X theirs: when the Actions bot rebuilt the same derived/data files (e.g.
  # registry manifests with timestamps), prefer the freshly scraped local
  # version instead of stopping on a conflict. Local scrape is the source of
  # truth for raw data; the bot rebuilds derived indexes again after push.
  if xgit fetch origin "$branch" 2>/dev/null; then
    xgit pull --rebase --autostash -X theirs origin "$branch" || {
      xgit rebase --abort >/dev/null 2>&1 || true
      echo "WARNING: rebase onto origin/$branch failed even with -X theirs;" >&2
      echo "         已回滚到 rebase 前状态,请手动检查后重试。" >&2
      exit 1
    }
  else
    echo "WARNING: 连不上 origin,跳过同步继续抓取(数据会先提交在本地)。" >&2
  fi
}

# push_with_retries <branch> — push, rebasing onto origin between attempts.
push_with_retries() {
  local branch="$1" attempt
  for attempt in 1 2 3; do
    if xgit push origin "$branch"; then return 0; fi
    echo "Push 失败(第 $attempt 次);rebase 远端后重试。" >&2
    xgit pull --rebase --autostash -X theirs origin "$branch" \
      || { xgit rebase --abort >/dev/null 2>&1 || true; }
    sleep $((attempt * 2))
  done
  return 1
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
  DATA_COMMITTED=true
  if [ "$PUSH" = "true" ]; then
    local branch
    branch="$(git rev-parse --abbrev-ref HEAD)"
    # The rebuild bot may have pushed while we were scraping; rebase and retry.
    if ! push_with_retries "$branch"; then
      echo "" >&2
      echo "⚠️  数据已安全提交在本地,只是推送没成功(网络问题居多)。" >&2
      echo "   网络/代理恢复后,双击一键抓取选「push」即可重推,无需重新抓取。" >&2
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

  push)
    # Re-push commits left behind by an earlier failed push. No scraping.
    branch="$(git rev-parse --abbrev-ref HEAD)"
    ahead="$(git rev-list --count "origin/${branch}..${branch}" 2>/dev/null || echo "?")"
    step "[2/3] 待推送提交:$ahead 个"
    if [ "$ahead" = "0" ]; then
      PUSH_SUMMARY="(没有待推送的提交)"
    else
      step "[3/3] 推送"
      DATA_COMMITTED=true
      if ! push_with_retries "$branch"; then
        echo "推送仍失败,请确认代理软件已开启后重试。" >&2
        exit 1
      fi
      DATA_COMMITTED=false
      PUSH_SUMMARY="已推送 $ahead 个提交"
      echo "Pushed. GitHub Actions 将重建索引并部署(约 3-5 分钟)。"
      echo "查看进度:$(repo_web_url)/actions"
    fi
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
    sed -n '2,43p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;

  *)
    echo "Unknown command: $command" >&2
    echo "Run 'Scripts/local/refresh.sh help' for usage." >&2
    exit 2
    ;;
esac
