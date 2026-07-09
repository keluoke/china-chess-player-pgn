#!/usr/bin/env bash
#
# Local scraping entrypoint. Run this on your own machine (residential IP) where
# chess-results.com and ratings.fide.com are reachable — GitHub's datacenter IPs
# are blocked, which is why the scraping half of the pipeline lives here instead
# of in Actions.
#
# What it does (NO-PULL design — this machine never pulls or rebases):
#   1. Runs the network-dependent scraper(s) for the chosen command.
#   2. Commits the RAW scraped data to the LOCAL history.
#   3. Force-pushes HEAD to the single-writer branch `local-data` — a force
#      push can never be rejected, so no pull/fetch/rebase is ever needed.
#   4. On GitHub, `ingest-local-data.yml` mirrors that branch's data tree
#      into main as the bot, then `rebuild-indexes.yml` rebuilds all derived
#      indexes and deploys the static site.
#
# So: scraping = local; merge + indexing + deploy = GitHub Actions.
# Conflicts are structurally impossible: this machine is the only producer
# of raw data, and it owns the local-data branch exclusively.
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
#   push         Re-push data left behind by an earlier failed push.
#   verify       Locally verify community evidence URLs (residential IP).
#   reindex      LOCAL pure rebuild (indexes/registry aliases/domestic). Optional;
#                normally Actions does this. No network.
#
# GitHub connectivity: scrapers always go DIRECT (residential IP required);
# git push auto-detects a working route — direct first, then the macOS system
# proxy (Veee/Clash/...), then well-known local proxy ports. Override with
# GITHUB_PROXY=http://127.0.0.1:PORT.
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

# ensure_pymod <import-name> [pip-name] — install a Python dep if missing.
# Tries default PyPI, then the Tsinghua mirror (mainland-friendly), with and
# without --break-system-packages (Homebrew Python is externally managed).
ensure_pymod() {
  local mod="$1" pkg="${2:-$1}"
  python3 -c "import $mod" 2>/dev/null && return 0
  step "安装 Python 依赖:$pkg"
  for args in "" "--break-system-packages" \
              "-i https://pypi.tuna.tsinghua.edu.cn/simple" \
              "-i https://pypi.tuna.tsinghua.edu.cn/simple --break-system-packages"; do
    # shellcheck disable=SC2086
    if python3 -m pip install --user --quiet $args "$pkg" 2>/dev/null \
       && python3 -c "import $mod" 2>/dev/null; then
      echo "已安装 $pkg"
      return 0
    fi
  done
  echo "无法自动安装 $pkg;请手动执行:python3 -m pip install --user $pkg" >&2
  return 1
}

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

# Proxies advertised in macOS System Settings (what Veee/Clash/V2Ray etc.
# register as the system proxy). GUI apps use these automatically; git does
# not, so we read them ourselves and hand them to git.
system_proxy_candidates() {
  command -v scutil >/dev/null 2>&1 || return 0
  scutil --proxies 2>/dev/null | awk '
    $1 == "HTTPSEnable" { httpsOn = $3 }
    $1 == "HTTPSProxy"  { httpsHost = $3 }
    $1 == "HTTPSPort"   { httpsPort = $3 }
    $1 == "SOCKSEnable" { socksOn = $3 }
    $1 == "SOCKSProxy"  { socksHost = $3 }
    $1 == "SOCKSPort"   { socksPort = $3 }
    $1 == "HTTPEnable"  { httpOn = $3 }
    $1 == "HTTPProxy"   { httpHost = $3 }
    $1 == "HTTPPort"    { httpPort = $3 }
    END {
      if (httpsOn == 1 && httpsHost != "") print "http://" httpsHost ":" httpsPort
      if (socksOn == 1 && socksHost != "") print "socks5h://" socksHost ":" socksPort
      if (httpOn == 1 && httpHost != "")   print "http://" httpHost ":" httpPort
    }'
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
  # Try, in order: macOS system proxy (Veee/Clash/... register here), shell
  # env proxies, then well-known local proxy ports.
  local p
  for p in $(system_proxy_candidates) \
           "${https_proxy:-}" "${HTTPS_PROXY:-}" \
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

# NO-PULL delivery: force-push HEAD to the single-writer branch. A force push
# to a branch only this machine writes can never be rejected, so no fetch,
# pull or rebase is ever needed (or performed) on this machine.
DATA_BRANCH="local-data"
PUSH_MARKER="$(git rev-parse --git-dir)/last-local-data-push"

push_with_retries() {
  local attempt
  detect_git_proxy || true
  for attempt in 1 2 3; do
    if xgit push --force origin "HEAD:refs/heads/${DATA_BRANCH}"; then
      git rev-parse HEAD > "$PUSH_MARKER" 2>/dev/null || true
      return 0
    fi
    echo "Push 失败(第 $attempt 次),稍候重试…" >&2
    sleep $((attempt * 3))
  done
  return 1
}

commit_and_push() {
  local message="$1"; shift
  step "[2/3] 提交(仅本地,不碰远端)"
  git add "$@"
  if git diff --cached --quiet; then
    echo "No changes to commit."
    # Still deliver anything a previous failed push left behind.
    if [ "$PUSH" = "true" ] && [ "$(cat "$PUSH_MARKER" 2>/dev/null)" != "$(git rev-parse HEAD)" ]; then
      step "[3/3] 推送(补推上次遗留数据)"
      DATA_COMMITTED=true
      push_with_retries || exit 1
      DATA_COMMITTED=false
      PUSH_SUMMARY="(无新数据;已补推上次遗留提交)"
    else
      PUSH_SUMMARY="(无新数据,未推送)"
    fi
    return 0
  fi
  local changed
  changed="$(git diff --cached --stat | tail -1)"
  git commit -m "$message"
  DATA_COMMITTED=true
  if [ "$PUSH" = "true" ]; then
    step "[3/3] 推送(免拉取,force-push 到 ${DATA_BRANCH} 分支)"
    if ! push_with_retries; then
      echo "" >&2
      echo "⚠️  数据已安全提交在本地,只是推送没成功(网络问题居多)。" >&2
      echo "   网络/代理恢复后,双击一键抓取选「push」即可重推,无需重新抓取。" >&2
      exit 1
    fi
    PUSH_SUMMARY="已推送($changed)"
    echo "Pushed. GitHub 将合入 main、重建索引并部署到 Cloudflare(约 3-5 分钟)。"
    echo "查看进度:$(repo_web_url)/actions"
  else
    PUSH_SUMMARY="已提交,未推送(--no-push)"
    echo "Committed locally (--no-push). 之后可选「push」交付。"
  fi
}

# No pull, ever: scrape → local commit → force-push to the data branch.
case "$command" in
  ""|-h|--help|help) : ;;
  push) REPORT_ON_EXIT=1 ;;
  *)
    REPORT_ON_EXIT=1
    step "[1/3] 抓取:$command(数据写入仓库内 docs/data/ 与 data/manual/,不碰远端)"
    ;;
esac

case "$command" in
  registry)
    py Scripts/sync_chinese_players.py ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Update Chinese player registry (local)" docs/data/registry data/generated
    ;;

  crawl)
    py Scripts/crawl_player_events.py --delay 1.0 --workers 2 --fetch-games ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Crawl Chess-Results player events (local)" data/manual data/generated docs/data
    ;;

  events)
    py Scripts/sync_chess_results_starting_rank_aliases.py --delay 1.0
    py Scripts/sync_chinese_players.py || py Scripts/apply_aliases_to_registry.py
    py Scripts/fetch_event_pgn.py --workers 3 --category li-chengzhi ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Ingest event archive names and PGN (local)" data/manual data/generated docs/data
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
    # zst 解压依赖:优先 python 模块,装不上时脚本内部还有 zstd CLI 兜底
    ensure_pymod zstandard || command -v zstd >/dev/null 2>&1 || exit 1
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
    commit_and_push "Routine local refresh (registry + crawl)" data/manual data/community data/generated docs/data
    ;;

  push)
    # Re-push data left behind by an earlier failed push. No scraping,
    # no pull — just force-push current HEAD to the data branch.
    if [ "$(cat "$PUSH_MARKER" 2>/dev/null)" = "$(git rev-parse HEAD)" ]; then
      step "本地数据已全部推送过,无需重推"
      PUSH_SUMMARY="(已是最新,未重推)"
    else
      step "推送(免拉取,force-push 到 ${DATA_BRANCH} 分支)"
      DATA_COMMITTED=true
      if ! push_with_retries; then
        echo "推送仍失败,请确认代理软件已开启后重试。" >&2
        exit 1
      fi
      DATA_COMMITTED=false
      PUSH_SUMMARY="已推送"
      echo "Pushed. GitHub 将合入 main、重建索引并部署(约 3-5 分钟)。"
      echo "查看进度:$(repo_web_url)/actions"
    fi
    ;;

  reindex)
    # Pure, no network — mirrors what rebuild-indexes.yml does on Actions.
    [ -f docs/data/registry/players.json ] && py Scripts/apply_aliases_to_registry.py || true
    py Scripts/sync_domestic_players.py
    py Scripts/sync_static_pgn.py
    py Scripts/build_static_player_pgn.py
    py Scripts/build_leaderboards.py
    [ -f Scripts/build_api.py ] && py Scripts/build_api.py
    [ -f Scripts/build_changelog.py ] && py Scripts/build_changelog.py
    [ -f Scripts/build_dashboard.py ] && py Scripts/build_dashboard.py
    commit_and_push "Rebuild derived indexes (local)" docs/data
    ;;

  verify)
    # Locally verify community evidence URLs (CI cannot reach chess-results).
    py Scripts/verify_community_sources.py ${EXTRA[@]+"${EXTRA[@]}"}
    commit_and_push "Verify community source URLs (local)" data/generated
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
