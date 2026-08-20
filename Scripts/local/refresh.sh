#!/usr/bin/env bash
#
# Maintainer-local data collection entrypoint.
#
# Collection happens only on this workstation.  Chess-Results is full-data:
# events are captured completely (players, pairings, results, standings, PGN),
# cleaned locally, compare-merged with the already-published copy and released
# through the manifest pipeline; raw HTML stays in the private per-run
# directory outside the repository.  FIDE, Lichess and Chess-Results releases
# are staged, validated, promoted, listed in an exact manifest, committed
# locally and force-pushed to the single-writer local-data branch.  GitHub
# never scrapes a source.
#
# Usage: Scripts/local/refresh.sh <command> [--no-push] [-- <extra args>]
#
# Safe commands:
#   health       Workstation, cache, worktree and provider connectivity checks.
#   all          Safe routine: monthly-due FIDE registry + top 3 private events.
#   registry     Download/validate FIDE and release the registry projection.
#   event-queue  Collect top 3 targets fully, clean, merge and release.
#   discover-events  Find recent tournament IDs via bounded FIDE-ID searches.
#   recover-events  Adopt orphaned validated event outputs; never re-scrapes.
#   storage-migrate  Upload/verify the current static PGN tree in R2 and release its receipt.
#   candidates   Collect starting-rank name candidates privately for review.
#   bulk         Mirror Lichess Broadcasts under CC BY-SA 4.0 and release.
#   bulk-full    Same as bulk, force-refresh every selected shard.
#   publish      Advance GitHub production delivery and opted-in Cloudflare shadow receipts.
#   deliver      CLI-compatible alias of publish; never re-scrapes.
#   receipts     Sync cloud ingest/rebuild/deploy receipts + online check.
#   shadow-publish  Advance opted-in Cloudflare shadow receipts only; never touches GitHub.
#   shadow-deliver  Double-write one existing outbox run to Cloudflare shadow ingest.
#   reindex      Offline diagnostic rebuild only; never commits or pushes.
#
# Retired/blocked by policy: crawl*, pgn*, events*, aliases, promote,
# reconcile, verify and contrib.  Community members submit target links and
# reviewed corrections; they do not execute or upload scraped payloads.
set -Eeo pipefail
umask 077

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

case "$command" in
  ""|-h|--help|help)
    sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

workspace_role="$(git -C "$REPO_ROOT" config --get chessdb.workspaceRole 2>/dev/null || true)"
if [ "$workspace_role" != "collector" ]; then
  printf 'WRONG_WORKSPACE_ROLE: 数据管线只能在 collector 工作区运行；当前角色为 %s。\n' \
    "${workspace_role:-unset}" >&2
  exit 2
fi

PYTHON_BIN="${CHINA_CHESS_PYTHON:-$(command -v python3)}"
py() { "$PYTHON_BIN" -u "$@"; }
py_extra() {
  if [ "${#EXTRA[@]}" -gt 0 ]; then
    py "$@" "${EXTRA[@]}"
  else
    py "$@"
  fi
}
reject_extra_flags() {
  local token blocked
  for token in "${EXTRA[@]}"; do
    for blocked in "$@"; do
      case "$token" in
        "$blocked"|"$blocked"=*)
          fail "UNSAFE_ARGUMENT_BLOCKED" "一键入口禁止覆盖内部参数 ${blocked}。"
          ;;
      esac
    done
  done
}
RUN_MANAGER="$REPO_ROOT/Scripts/local/run_manager.py"
export CHINA_CHESS_MAINTAINER_LOCAL=1
# Full-data is the contract standard (AGENTS.md): cleaned, structured event
# data is published for completeness; raw HTML never leaves the private run
# area.  Pin the policy explicitly so an inherited link-only environment
# cannot silently withhold publication.
export CHESS_RESULTS_RELEASE_POLICY=full-data
unset CHESS_RESULTS_PUBLICATION_AUTHORIZED || true

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "${BOLD}${CYAN}" "$*" "$RESET"; }

notify_mac() {
  [ "${CHINA_CHESS_DISABLE_NOTIFICATIONS:-}" = "1" ] && return 0
  command -v osascript >/dev/null 2>&1 || return 0
  osascript -e "display notification \"$1\" with title \"棋手数据刷新\"" >/dev/null 2>&1 || true
}

ERROR_CODE=""
ERROR_MESSAGE=""
PUSH_SUMMARY=""
DATA_COMMITTED=false
DELIVERY_PENDING=false
DELIVERED_COUNT=0
DELIVERY_ATTENTION_COUNT=0
SHADOW_SUMMARY=""
RUN_DIR=""

acquire_args=(--command "$command" --pid "$$")
if [ "$command" = "event-queue" ]; then
  for token in "${EXTRA[@]}"; do
    # Use argparse's --option=value form so queue flags such as --from-queue
    # are persisted as values instead of being reinterpreted as acquire flags.
    acquire_args+=(--request-argument="$token")
  done
fi
RUN_DIR="$(py "$RUN_MANAGER" acquire "${acquire_args[@]}")" || exit $?
RUN_LOG="$RUN_DIR/run.log"
touch "$RUN_LOG"
exec >> "$RUN_LOG" 2>&1

CURRENT_STAGE="starting"

state() {
  CURRENT_STAGE="$1"
  py "$RUN_MANAGER" update --run-dir "$RUN_DIR" --stage "$1" --message "$2" >/dev/null
}

fail() {
  ERROR_CODE="$1"
  ERROR_MESSAGE="$2"
  py "$RUN_MANAGER" error --run-dir "$RUN_DIR" --stage "$CURRENT_STAGE" \
    --code "$ERROR_CODE" --message "$ERROR_MESSAGE" >/dev/null 2>&1 || true
  printf '%s%s: %s%s\n' "$RED" "$ERROR_CODE" "$ERROR_MESSAGE" "$RESET" >&2
  exit "${3:-1}"
}

partial() {
  ERROR_CODE="$1"
  ERROR_MESSAGE="$2"
  py "$RUN_MANAGER" error --run-dir "$RUN_DIR" --stage "$CURRENT_STAGE" \
    --code "$ERROR_CODE" --message "$ERROR_MESSAGE" >/dev/null 2>&1 || true
  printf '%s%s: %s%s\n' "$YELLOW" "$ERROR_CODE" "$ERROR_MESSAGE" "$RESET" >&2
  exit 4
}

on_signal() {
  ERROR_CODE="INTERRUPTED"
  ERROR_MESSAGE="任务已由用户中止；未完成的暂存运行不会发布。"
  py "$RUN_MANAGER" error --run-dir "$RUN_DIR" --stage "$CURRENT_STAGE" \
    --code "$ERROR_CODE" --message "$ERROR_MESSAGE" >/dev/null 2>&1 || true
  exit 130
}
trap on_signal INT TERM

on_exit() {
  status=$?
  trap - EXIT INT TERM
  set +e
  result="ok"
  message="${PUSH_SUMMARY:-任务完成}"
  if [ "$status" -ne 0 ]; then
    structured="$(py "$RUN_MANAGER" error-get --run-dir "$RUN_DIR" --plain 2>/dev/null || true)"
    if [ -n "$structured" ]; then
      IFS=$'\t' read -r structured_code structured_message <<< "$structured"
      [ -n "$structured_code" ] && ERROR_CODE="$structured_code"
      [ -n "$structured_message" ] && ERROR_MESSAGE="$structured_message"
    fi
    if [ "$status" -eq 4 ] && [ "$ERROR_CODE" = "PARTIAL_FAILURE" ]; then
      result="partial"
      message="${ERROR_MESSAGE:-部分目标需要处理；完整目标和发布结果已保留。}"
    elif [ "$DATA_COMMITTED" = "true" ]; then
      result="push-failed"
      ERROR_CODE="${ERROR_CODE:-GIT_PUSH_FAILED}"
      message="数据已按 manifest 提交在本地，投递失败；使用 deliver 重投即可。"
    else
      result="failed"
      ERROR_CODE="${ERROR_CODE:-UNEXPECTED_FAILURE}"
      message="${ERROR_MESSAGE:-任务失败，请查看本次运行日志。}"
    fi
  else
    # State labels set a provisional code (for example
    # RELEASE_VALIDATION_FAILED) before an operation runs. A successful run
    # must not leave that stale failure code in the monitoring API.
    ERROR_CODE=""
  fi
  finish_error="$RUN_DIR/diagnostics/final-state-error.log"
  finish_ok=false
  finish_attempt=1
  while [ "$finish_attempt" -le 3 ]; do
    if py "$RUN_MANAGER" finish --run-dir "$RUN_DIR" --code "$status" \
      --result "$result" --error-code "$ERROR_CODE" --message "$message" \
      >/dev/null 2>>"$finish_error"; then
      finish_ok=true
      break
    fi
    finish_attempt=$((finish_attempt + 1))
  done
  if [ "$finish_ok" != "true" ]; then
    status=5
    ERROR_CODE="FINAL_STATE_WRITE_FAILED"
    if [ "$command" = "discover-events" ] && [ -s "$RUN_DIR/result.json" ]; then
      message="赛事发现结果已保留；仅运行状态收尾异常，无需重新查询来源。"
    else
      message="任务进程已结束，但最终运行状态连续 3 次写入失败；请查看 diagnostics/final-state-error.log。"
    fi
    printf '\n%s%s：%s%s\n' "$YELLOW" "$ERROR_CODE" "$message" "$RESET" >&2
  fi
  if [ "$status" -eq 0 ]; then
    printf '\n%s✅ 完成：%s %s%s\n' "$GREEN" "$command" "$PUSH_SUMMARY" "$RESET"
    notify_mac "完成：$command ✅"
  elif [ "$status" -eq 4 ] && [ "$ERROR_CODE" = "PARTIAL_FAILURE" ]; then
    printf '\n%s⚠️ 部分完成：%s%s\n' "$YELLOW" "$message" "$RESET"
    notify_mac "部分完成：$command ⚠️"
  elif [ "$DATA_COMMITTED" = "true" ]; then
    printf '\n%s⚠️ 数据已提交本地，投递失败；稍后运行 publish。%s\n' "$RED" "$RESET"
    notify_mac "数据已保存，推送失败 ⚠️"
  else
    printf '\n%s❌ %s：%s%s\n' "$RED" "$ERROR_CODE" "$message" "$RESET"
    notify_mac "失败：$command ($ERROR_CODE)"
  fi
  exit "$status"
}
trap on_exit EXIT

# --- dependencies ---------------------------------------------------------
ensure_pymod() {
  local mod="$1" pkg="${2:-$1}" attempt label args runtime_root venv_python
  py -c "import $mod" 2>/dev/null && return 0
  step "安装 Python 依赖：$pkg"
  if [ -n "${CHINA_CHESS_LOCAL_ROOT:-}" ]; then
    runtime_root="$CHINA_CHESS_LOCAL_ROOT/python-runtime"
  elif [ "$(uname -s)" = "Darwin" ]; then
    runtime_root="$HOME/Library/Application Support/ChinaChessPlayerPGN/python-runtime"
  else
    runtime_root="${XDG_STATE_HOME:-$HOME/.local/state}/china-chess-player-pgn/python-runtime"
  fi
  venv_python="$runtime_root/bin/python3"
  if [ ! -x "$venv_python" ]; then
    echo "创建隔离 Python 运行环境：$runtime_root"
    "$PYTHON_BIN" -m venv "$runtime_root" || \
      fail "DEPENDENCY_ENVIRONMENT_FAILED" "无法创建隔离 Python 运行环境：$runtime_root"
  fi
  if "$venv_python" -c "import $mod" 2>/dev/null; then
    PYTHON_BIN="$venv_python"
    return 0
  fi
  for attempt in \
    "PyPI|" \
    "清华镜像|-i https://pypi.tuna.tsinghua.edu.cn/simple"; do
    label="${attempt%%|*}"
    args="${attempt#*|}"
    echo "依赖安装尝试：${label}（单次网络超时 12 秒）"
    # shellcheck disable=SC2086
    if "$venv_python" -m pip install --quiet --disable-pip-version-check \
       --default-timeout=12 --retries=1 $args "$pkg" \
       && "$venv_python" -c "import $mod"; then
      PYTHON_BIN="$venv_python"
      return 0
    fi
  done
  fail "DEPENDENCY_INSTALL_FAILED" "无法安装 ${pkg}，请手动安装后重试。"
}

# --- GitHub delivery (scrapers never inherit these proxy settings) --------
# The proxy is passed per-invocation to curl/git only; it is never exported,
# so Chess-Results/FIDE/Lichess requests always stay on the residential IP.
GITHUB_PROBE_URL=""
LAST_PUSH_ERROR=""
GIT_PUSH_TIMEOUT="${GIT_PUSH_TIMEOUT:-50}"

github_probe_url() {
  if [ -z "$GITHUB_PROBE_URL" ]; then
    local remote repo
    remote="$(git remote get-url origin 2>/dev/null || true)"
    repo="$(printf '%s' "$remote" | sed -nE 's#.*github\.com[/:]([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')"
    if [ -n "$repo" ]; then
      GITHUB_PROBE_URL="https://github.com/${repo}.git/info/refs?service=git-upload-pack"
    else
      GITHUB_PROBE_URL="https://github.com/"
    fi
  fi
  printf '%s' "$GITHUB_PROBE_URL"
}

github_ok() {
  # Probe Git smart HTTP, not the github.com landing page. Inspecting the
  # HTTP status keeps 5xx or local gateway error pages from counting as ok;
  # 401 still proves the route reaches GitHub's Git endpoint.
  local status
  if [ -n "$1" ]; then
    status="$(curl -s -o /dev/null --max-time 6 -x "$1" -w '%{http_code}' "$(github_probe_url)" || true)"
  else
    status="$(curl -s -o /dev/null --max-time 6 -w '%{http_code}' "$(github_probe_url)" || true)"
  fi
  case "$status" in
    200|301|401) return 0 ;;
    *) return 1 ;;
  esac
}

system_proxy_candidates() {
  command -v scutil >/dev/null 2>&1 || return 0
  scutil --proxy 2>/dev/null | awk '
    $1 == "HTTPSEnable" { httpsOn = $3 }
    $1 == "HTTPSProxy"  { httpsHost = $3 }
    $1 == "HTTPSPort"   { httpsPort = $3 }
    $1 == "SOCKSEnable" { socksOn = $3 }
    $1 == "SOCKSProxy"  { socksHost = $3 }
    $1 == "SOCKSPort"   { socksPort = $3 }
    END {
      if (httpsOn == 1 && httpsHost != "") print "http://" httpsHost ":" httpsPort
      if (socksOn == 1 && socksHost != "") print "socks5h://" socksHost ":" socksPort
    }'
}

github_routes() {
  # direct → explicit GITHUB_PROXY → macOS system proxies → env → common local
  # proxies. Deduplicated, one route per line ("" = direct).
  {
    echo ""
    [ -n "${GITHUB_PROXY:-}" ] && echo "$GITHUB_PROXY"
    system_proxy_candidates
    [ -n "${https_proxy:-}" ] && echo "$https_proxy"
    [ -n "${HTTPS_PROXY:-}" ] && echo "$HTTPS_PROXY"
    echo "http://127.0.0.1:7890"
    echo "http://127.0.0.1:1087"
    echo "socks5h://127.0.0.1:1080"
    echo "http://127.0.0.1:8118"
  } | awk '!seen[$0]++'
}

xgit() {
  local proxy="$1"; shift
  # A successful six-second smart-HTTP probe does not guarantee that a later
  # `git push` will finish.  Bound every route so an ISP/proxy half-open
  # connection cannot hold the event collector forever.  Start a new process
  # group and terminate the whole Git helper tree on expiry.
  python3 - "$proxy" "$GIT_PUSH_TIMEOUT" "$@" <<'PY'
import os
import signal
import subprocess
import sys

proxy, raw_timeout, *args = sys.argv[1:]
try:
    timeout = max(1, int(raw_timeout))
except ValueError:
    timeout = 50
command = ["git"]
if proxy:
    command += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
command += args
process = subprocess.Popen(command, start_new_session=True)
try:
    raise SystemExit(process.wait(timeout=timeout))
except subprocess.TimeoutExpired:
    sys.stderr.write(f"GIT_PUSH_TIMEOUT: Git 路线超过 {timeout} 秒未完成，已终止。\n")
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    raise SystemExit(124)
PY
}

classify_git_error() {
  local log="$1"
  if printf '%s' "$log" | grep -qiE 'could not resolve host|name or service not known'; then
    echo "GIT_DNS_FAILURE"
  elif printf '%s' "$log" | grep -qiE 'ssl|tls|certificate'; then
    echo "GIT_TLS_FAILURE"
  elif printf '%s' "$log" | grep -qiE 'proxy'; then
    echo "GIT_PROXY_FAILURE"
  elif printf '%s' "$log" | grep -qiE 'authentication failed|permission denied|http 401|http 403|access denied'; then
    echo "GIT_AUTH_FAILED"
  elif printf '%s' "$log" | grep -qiE 'pre-receive hook|protected branch|remote rejected'; then
    echo "GIT_REMOTE_REJECTED"
  elif printf '%s' "$log" | grep -qiE 'timed out|failed to connect|connection (refused|reset)'; then
    echo "GIT_CONNECT_FAILURE"
  else
    echo "GIT_PUSH_FAILED"
  fi
}

DATA_BRANCH="local-data"
STATE_ROOT="$(python3 -c 'import pathlib,sys; sys.path.insert(0,"Scripts"); from source_policy import local_state_root; print(local_state_root())')"
PUSH_MARKER="$STATE_ROOT/last-local-data-push"

bundle_base_commit() {
  python3 - "$STATE_ROOT/outbox/$1/manifest.json" <<'PY'
import json, pathlib, sys
try:
    print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get("baseCommit") or "")
except Exception:
    print("")
PY
}

push_commit_with_routes() {
  # Push an exact, immutable commit SHA to the single-writer branch, rotating
  # to the next route on failure instead of retrying the same dead route.
  local sha="$1" run_id="$2" route err classification remote_main base_commit
  base_commit="$(bundle_base_commit "$run_id")"
  LAST_PUSH_ERROR=""
  while IFS= read -r route; do
    if ! github_ok "$route"; then
      echo "GitHub 路线 ${route:-direct} smart HTTP 不可达，跳过。" >&2
      continue
    fi
    [ -n "$route" ] && echo "GitHub 使用路线 ${route}；数据来源仍保持住宅 IP 直连。"
    remote_main="$(xgit "$route" ls-remote origin refs/heads/main 2>/dev/null | awk 'NR==1 {print $1}')"
    if [ -z "$base_commit" ] || [ "$remote_main" != "$base_commit" ]; then
      LAST_PUSH_ERROR="GIT_FALLBACK_BASE_MISMATCH"
      echo "Git 路线跳过：发布包基线 ${base_commit:-missing} 与远端 main ${remote_main:-unavailable} 不一致。" >&2
      continue
    fi
    if err="$(xgit "$route" push --force origin "${sha}:refs/heads/${DATA_BRANCH}" 2>&1)"; then
      printf '%s\n' "$err"
      mkdir -p "$(dirname "$PUSH_MARKER")"
      printf '%s\n' "$sha" > "$PUSH_MARKER"
      return 0
    fi
    classification="$(classify_git_error "$err")"
    LAST_PUSH_ERROR="$classification"
    printf '%s\n' "$err" | tail -5 >&2
    echo "路线 ${route:-direct} 推送失败（${classification}）。" >&2
    case "$classification" in
      GIT_AUTH_FAILED|GIT_REMOTE_REJECTED)
        # Rotating routes cannot fix auth or remote policy failures.
        return 1
        ;;
    esac
  done < <(github_routes)
  return 1
}

classify_api_delivery_error() {
  local output="$1" code
  code="$(printf '%s' "$output" | grep -Eo 'RELEASE_BASE_[A-Z_]+|RELEASE_HASH_MISMATCH|RELEASE_MANIFEST_INVALID|RELEASE_PATH_[A-Z_]+|API_DELIVERY_BLOCKED|API_DELIVERY_BASELINE_MISSING|API_DELIVERY_TREE_TRUNCATED' | tail -1)"
  if [ -n "$code" ]; then
    printf 'policy\t%s\n' "$code"
  else
    printf 'transport\tGITHUB_API_TRANSPORT_FAILED\n'
  fi
}

api_deliver() {
  # Prefer the API for baseline-aware bundles. It sends only the exact
  # manifest files, avoiding an expensive Git history walk after collection.
  # The same validate_manifest/three-way baseline policy applies.
  local run_id="$1" output classification
  API_DELIVERY_CLASS="transport"
  API_DELIVERY_ERROR="GITHUB_API_UNAVAILABLE"
  command -v gh >/dev/null 2>&1 || return 1
  gh auth status >/dev/null 2>&1 || return 1
  echo "优先使用 GitHub Git Database API 投递 ${run_id}。"
  if output="$(py Scripts/local/publish_data_via_api.py --outbox "$run_id" 2>&1)"; then
    printf '%s\n' "$output"
    return 0
  fi
  printf '%s\n' "$output" >&2
  classification="$(classify_api_delivery_error "$output")"
  IFS=$'\t' read -r API_DELIVERY_CLASS API_DELIVERY_ERROR <<< "$classification"
  return 1
}

deliver_outbox() {
  # Deliver retryable pending bundles oldest first. Attention bundles remain
  # immutable and visible, but never block a later independent bundle.
  local line run_id sha error delivered=0 attention=0 failed=0
  while IFS=$'\t' read -r run_id sha; do
    [ -n "$run_id" ] || continue
    echo "投递 outbox release ${run_id}（commit ${sha}）"
    if api_deliver "$run_id"; then
      delivered=$((delivered + 1))
    elif [ "$API_DELIVERY_CLASS" = "transport" ] && push_commit_with_routes "$sha" "$run_id"; then
      py "$RUN_MANAGER" outbox-update --run-id "$run_id" --status pushed \
        --remote-sha "$sha" --route git >/dev/null
      delivered=$((delivered + 1))
    else
      if [ "$API_DELIVERY_CLASS" = "policy" ]; then
        LAST_PUSH_ERROR="$API_DELIVERY_ERROR"
      fi
      error="${LAST_PUSH_ERROR:-${API_DELIVERY_ERROR:-GIT_PUSH_FAILED}}"
      py "$RUN_MANAGER" outbox-update --run-id "$run_id" --status pending \
        --error "$error" >/dev/null || true
      if [ "$API_DELIVERY_CLASS" = "policy" ] || [ "$error" = "GIT_FALLBACK_BASE_MISMATCH" ]; then
        attention=$((attention + 1))
        echo "WARNING: 发布包 ${run_id} 已因 ${error} 转入人工关注；继续推进后续独立包。" >&2
        continue
      fi
      failed=$((failed + 1))
      case "$error" in
        GIT_AUTH_FAILED|GIT_REMOTE_REJECTED) break ;;
      esac
    fi
  done < <(py "$RUN_MANAGER" outbox-list --status pending --retryable-only --plain)
  DELIVERED_COUNT="$delivered"
  DELIVERY_ATTENTION_COUNT="$attention"
  [ "$failed" -eq 0 ]
}

shadow_status() {
  python3 - "$STATE_ROOT/outbox/$1/shadow-delivery.json" <<'PY'
import json, pathlib, sys
try:
    print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get("status") or "")
except Exception:
    print("")
PY
}

shadow_auto_enabled() {
  [ "${CLOUDFLARE_SHADOW_AUTO:-}" = "1" ] && return 0
  [ "${CLOUDFLARE_SHADOW_AUTO:-}" = "0" ] && return 1
  python3 - "$STATE_ROOT/automation.json" <<'PY'
import json, pathlib, sys
try:
    enabled = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get("shadowEnabled") is True
except Exception:
    enabled = False
raise SystemExit(0 if enabled else 1)
PY
}

pause_shadow_automation() {
  local reason="$1"
  python3 - "$STATE_ROOT/automation.json" "$reason" <<'PY'
import datetime, json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    payload = {}
payload.update({
    "schemaVersion": 1,
    "shadowEnabled": False,
    "shadowPausedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "shadowPauseReason": sys.argv[2],
})
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(tmp, path)
PY
}

shadow_error_code() {
  python3 - "$STATE_ROOT/outbox/$1/shadow-delivery.json" <<'PY'
import json, pathlib, sys
try:
    print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get("errorCode") or "")
except Exception:
    print("")
PY
}

shadow_deliver_one() {
  local run_id="$1" wait_seconds="${2:-30}" force="${3:-false}" status
  status="$(shadow_status "$run_id")"
  case "$status" in
    complete|conflict|failed|ineligible) return 0 ;;
    "") [ "$force" = "true" ] || return 0 ;;
  esac
  if CLOUDFLARE_INGEST_SINGLE_ATTEMPT=1 CLOUDFLARE_INGEST_REQUEST_TIMEOUT=15 \
      py Scripts/local/cloudflare_ingest.py --run-id "$run_id" \
      --wait-seconds "$wait_seconds" --accept-queued; then
    SHADOW_SUMMARY="Cloudflare 影子已接收或完成"
    return 0
  fi
  status="$(shadow_status "$run_id")"
  case "$status" in
    conflict) SHADOW_SUMMARY="Cloudflare 影子发现基线冲突，生产 GitHub 不受影响" ;;
    ineligible)
      SHADOW_SUMMARY="发布包超过影子免费层单包门禁，仅继续 GitHub 生产发布"
      echo "WARNING: ${SHADOW_SUMMARY}（run ${run_id}）。" >&2
      return 0
      ;;
    *) SHADOW_SUMMARY="Cloudflare 影子暂不可用，已保留独立状态" ;;
  esac
  echo "WARNING: ${SHADOW_SUMMARY}（run ${run_id}）。" >&2
  return 1
}

shadow_retry_existing() {
  local line run_id sha error retried=0
  while IFS=$'\t' read -r run_id sha; do
    [ -n "$run_id" ] || continue
    [ -f "$STATE_ROOT/outbox/$run_id/shadow-delivery.json" ] || continue
    case "$(shadow_status "$run_id")" in
      complete|conflict|failed|ineligible) continue ;;
    esac
    if ! shadow_deliver_one "$run_id" 30 true; then
      error="$(shadow_error_code "$run_id")"
      error="${error:-CLOUDFLARE_INGEST_UNAVAILABLE}"
      pause_shadow_automation "$error"
      SHADOW_SUMMARY="Cloudflare 影子因 ${error} 已自动暂停；GitHub 生产继续"
      echo "WARNING: ${SHADOW_SUMMARY}。" >&2
      return 1
    fi
    retried=$((retried + 1))
  done < <(py "$RUN_MANAGER" outbox-list --plain)
  [ "$retried" -gt 0 ] && SHADOW_SUMMARY="已推进 ${retried} 个 Cloudflare 影子回执" || true
}

commit_prepared_release() {
  local message="$1"
  if git diff --cached --quiet; then
    PUSH_SUMMARY="无新的公开发布文件；私有采集结果已保存在 $RUN_DIR"
    return 0
  fi
  state "committing" "按 release manifest 精确提交"
  changed="$(git diff --cached --stat | tail -1)"
  git commit -m "$message" || return $?
  # The immutable bundle (manifest + hashed files + delivery state) makes
  # collection and GitHub delivery independent: a push failure never requires
  # re-scraping, and multiple pending releases can queue up.
  py "$RUN_MANAGER" outbox-save --repo "$REPO_ROOT" --run-dir "$RUN_DIR" \
    --commit "$(git rev-parse HEAD)" >/dev/null || return $?
  DATA_COMMITTED=true
  run_id="$(basename "$RUN_DIR")"
  # Shadow auto-write is opt-in from the loopback-only panel.  Until the
  # maintainer explicitly enables it, new bundles continue to use only the
  # established GitHub production path.
  if shadow_auto_enabled; then
    state "shadow-delivering" "将同一 outbox 自动双写到 Cloudflare 免费层影子 ingest"
    shadow_deliver_one "$run_id" 0 true || true
  fi
  if [ "$PUSH" = "true" ]; then
    state "delivering" "投递 outbox 发布包到单写者 local-data 分支"
    if ! deliver_outbox; then
      # Collection is already durable: a transport outage must not turn it
      # into a failed scrape or force the maintainer to recapture sources.
      DELIVERY_PENDING=true
      DATA_COMMITTED=false
      PUSH_SUMMARY="采集与本地提交完成；发布包处于 delivery-pending，面板可稍后重投"
      echo "WARNING: GitHub 暂不可用；采集数据已安全保存在 outbox，任务继续按采集结果完成。" >&2
      return 0
    fi
    DATA_COMMITTED=false
    PUSH_SUMMARY="已发布 $changed"
  else
    DATA_COMMITTED=false
    PUSH_SUMMARY="已提交 ${changed}；未推送（--no-push），发布包已保存到 outbox"
  fi
}

prepare_release() {
  local release_command="$1"; shift
  args=()
  while [ "$#" -gt 0 ]; do
    args+=(--allow "$1")
    shift
  done
  state "validating" "生成精确发布 manifest 并校验边界"
  py "$RUN_MANAGER" prepare --repo "$REPO_ROOT" --run-dir "$RUN_DIR" \
    --command "$release_command" "${args[@]}"
}

preflight_release() {
  args=()
  while [ "$#" -gt 0 ]; do
    args+=(--allow "$1")
    shift
  done
  state "preflight" "检查发布路径、暂存区、磁盘与运行锁"
  py "$RUN_MANAGER" preflight --repo "$REPO_ROOT" --run-dir "$RUN_DIR" "${args[@]}"
}

# --- isolated collectors --------------------------------------------------
REGISTRY_PATHS=(
  "docs/data/registry"
  "data/generated/federation-snapshots"
  "data/generated/transfer-candidates.json"
)
BULK_PATHS=(
  # Immutable .pgn.zst shards remain local/R2 and are deliberately excluded
  # from Git manifests even though they live below docs/data/bulk locally.
  "docs/data/bulk/manifest.json"
  "docs/data/bulk/lichess-broadcast/manifest.json"
  "docs/data/bulk/youth"
  "docs/data/bulk/lichess-events"
)
EVENT_PATHS=(
  "data/generated/chess-results-event-details"
  "data/generated/chess-results-event-pgn"
  "data/generated/pgn-source-attempts"
  "docs/data/pgn/chess-results"
  "data/generated/r2-object-receipts/events--chess-results.json"
)

run_registry() {
  reject_extra_flags \
    --output-root --snapshot-dir --transfer-candidates --previous-registry \
    --manual-aliases --federation --max-players --dry-run
  preflight_release "${REGISTRY_PATHS[@]}" || return $?
  local staging="$RUN_DIR/staging/fide"
  rm -rf "$staging" || return $?
  mkdir -p "$staging/registry" "$staging/generated/federation-snapshots" || return $?
  if [ -d data/generated/federation-snapshots ]; then
    cp -R data/generated/federation-snapshots/. "$staging/generated/federation-snapshots/" || return $?
  fi
  state "downloading" "下载并验证 FIDE ZIP；失败时回退 last-good"
  py_extra Scripts/sync_chinese_players.py \
    --output-root "$staging/registry" \
    --snapshot-dir "$staging/generated/federation-snapshots" \
    --transfer-candidates "$staging/generated/transfer-candidates.json" || return $?
  state "validating" "校验 registry 权威字段、分片一致性和姓名勘误"
  py Scripts/validate_registry_release.py \
    --registry "$staging/registry" \
    --corrections data/community/name-corrections.csv || return $?
  state "promoting" "原子晋升已校验的 FIDE 暂存输出"
  py "$RUN_MANAGER" promote --repo "$REPO_ROOT" --run-dir "$RUN_DIR" \
    --tree "$staging/registry::docs/data/registry" \
    --tree "$staging/generated/federation-snapshots::data/generated/federation-snapshots" \
    --file "$staging/generated/transfer-candidates.json::data/generated/transfer-candidates.json" || return $?
  prepare_release registry "${REGISTRY_PATHS[@]}" || return $?
}

R2_PGN_RECEIPT="data/generated/r2-object-receipts/events--chess-results.json"

upload_event_archives_to_r2() {
  ensure_pymod boto3
  local source_root="${R2_EVENT_PGN_SOURCE_ROOT:-$REPO_ROOT/data/generated/chess-results-event-pgn}"
  local secrets_file="${R2_SECRETS_FILE:-$REPO_ROOT/.secrets.local}"
  [ -d "$source_root" ] || fail "R2_EVENT_SOURCE_MISSING" "R2 赛事 PGN 源目录不存在：$source_root"
  [ -f "$secrets_file" ] || fail "R2_SECRETS_MISSING" "R2 凭据文件不存在：$secrets_file"
  state "uploading-r2-events" "上传赛事 PGN 归档并逐对象回读 SHA-256"
  py Scripts/local/upload_bulk_to_r2.py \
    --prefix events/chess-results \
    --source-root "$source_root" \
    --secrets "$secrets_file" \
    --receipt-path "$REPO_ROOT/$R2_PGN_RECEIPT" \
    --receipt-field objects \
    --workers "${R2_UPLOAD_WORKERS:-24}" || return $?
  state "verifying-r2-events" "全量 HEAD 校验赛事 PGN 对象元数据与本地 SHA-256"
  py Scripts/local/upload_bulk_to_r2.py \
    --prefix events/chess-results \
    --source-root "$source_root" \
    --secrets "$secrets_file" \
    --receipt-path "$REPO_ROOT/$R2_PGN_RECEIPT" \
    --receipt-field objects \
    --workers "${R2_UPLOAD_WORKERS:-24}" \
    --verify || return $?
}

upload_selected_r2_files() {
  local source_root="$1" prefix="$2" receipt_field="$3" file_list="$4" label="$5"
  [ -s "$file_list" ] || return 0
  ensure_pymod boto3
  local secrets_file="${R2_SECRETS_FILE:-$REPO_ROOT/.secrets.local}"
  [ -d "$source_root" ] || fail "R2_SOURCE_MISSING" "R2 ${label}源目录不存在：$source_root"
  [ -f "$secrets_file" ] || fail "R2_SECRETS_MISSING" "R2 凭据文件不存在：$secrets_file"
  state "uploading-r2-selected" "上传本次 ${label} 并逐对象回读 SHA-256"
  py Scripts/local/upload_bulk_to_r2.py \
    --prefix "$prefix" \
    --source-root "$source_root" \
    --file-list "$file_list" \
    --secrets "$secrets_file" \
    --receipt-path "$REPO_ROOT/$R2_PGN_RECEIPT" \
    --receipt-field "$receipt_field" \
    --workers "${R2_UPLOAD_WORKERS:-24}" || return $?
  state "verifying-r2-selected" "HEAD 校验本次 ${label} 的对象元数据与本地 SHA-256"
  py Scripts/local/upload_bulk_to_r2.py \
    --prefix "$prefix" \
    --source-root "$source_root" \
    --file-list "$file_list" \
    --secrets "$secrets_file" \
    --receipt-path "$REPO_ROOT/$R2_PGN_RECEIPT" \
    --receipt-field "$receipt_field" \
    --workers "${R2_UPLOAD_WORKERS:-24}" \
    --verify || return $?
}

run_r2_migration() {
  [ "${#EXTRA[@]}" -eq 0 ] || fail "UNSAFE_ARGUMENT_BLOCKED" "storage-migrate 不接受额外参数。"
  preflight_release "$R2_PGN_RECEIPT" || return $?
  upload_event_archives_to_r2 || return $?
  ensure_pymod boto3
  local source_root="${R2_PGN_SOURCE_ROOT:-$REPO_ROOT/docs/data/pgn}"
  local secrets_file="${R2_SECRETS_FILE:-$REPO_ROOT/.secrets.local}"
  [ -d "$source_root" ] || fail "R2_SOURCE_MISSING" "R2 PGN 源目录不存在：$source_root"
  [ -f "$secrets_file" ] || fail "R2_SECRETS_MISSING" "R2 凭据文件不存在：$secrets_file"
  state "uploading-r2" "并行上传全量静态 PGN，逐对象回读 SHA-256"
  py Scripts/local/upload_bulk_to_r2.py \
    --prefix data/pgn \
    --source-root "$source_root" \
    --secrets "$secrets_file" \
    --receipt-path "$REPO_ROOT/$R2_PGN_RECEIPT" \
    --receipt-field playerObjects \
    --workers "${R2_UPLOAD_WORKERS:-24}" || return $?
  state "verifying-r2" "全量 HEAD 校验 R2 对象元数据与本地 SHA-256"
  py Scripts/local/upload_bulk_to_r2.py \
    --prefix data/pgn \
    --source-root "$source_root" \
    --secrets "$secrets_file" \
    --receipt-path "$REPO_ROOT/$R2_PGN_RECEIPT" \
    --receipt-field playerObjects \
    --workers "${R2_UPLOAD_WORKERS:-24}" \
    --verify || return $?
  prepare_release storage-migrate "$R2_PGN_RECEIPT" || return $?
}

run_private_events() {
  # Full-data collection: capture completely, clean locally, compare-merge
  # with the published copy and write only changed events into the release
  # paths. Raw HTML stays in $RUN_DIR. Derived indexes are rebuilt in the
  # cloud after ingest (--no-players --no-rebuild).
  # Exit code 4 = partial batch: some targets failed but were isolated and
  # recorded; completed targets are checkpointed, published and never
  # re-scraped.
  reject_extra_flags --private-root --publish --authorized-publication \
    --no-players --no-rebuild
  preflight_release "${EVENT_PATHS[@]}" || return $?
  state "collecting" "按队列采集 Chess-Results 全量赛事数据；本地清洗后与已发布副本比对合并"
  local rc=0
  if [ "${#EXTRA[@]}" -eq 0 ]; then
    py Scripts/sync_chess_results_event.py --from-queue 3 --private-root "$RUN_DIR" \
      --publish --no-players --no-rebuild || rc=$?
  else
    py_extra Scripts/sync_chess_results_event.py --private-root "$RUN_DIR" \
      --publish --no-players --no-rebuild || rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    PUSH_SUMMARY="赛事采集完成；变化的清洗数据已进入发布路径"
  elif [ "$rc" -eq 4 ]; then
    PUSH_SUMMARY="赛事采集部分完成；失败目标已隔离记录，成功赛事已清洗并进入发布路径"
  else
    ERROR_MESSAGE="赛事采集未形成可完成目标；逐场真实原因见本批结果、capture-state 与本次日志。"
  fi
  # Git ignores these machine roots, so derive the exact R2 delta from the
  # same recovery projection used by preflight/manifest preparation.
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 4 ]; then
    local event_r2_list="$RUN_DIR/diagnostics/r2-event-files.txt"
    local player_r2_list="$RUN_DIR/diagnostics/r2-player-files.txt"
    mkdir -p "$RUN_DIR/diagnostics"
    py "$RUN_MANAGER" recovery-list --repo "$REPO_ROOT" \
      --allow "${EVENT_PATHS[1]}" --plain | \
      sed 's#^data/generated/chess-results-event-pgn/##' > "$event_r2_list"
    py "$RUN_MANAGER" recovery-list --repo "$REPO_ROOT" \
      --allow "${EVENT_PATHS[3]}" --plain | \
      sed 's#^docs/data/pgn/##' > "$player_r2_list"
    upload_selected_r2_files \
      "${R2_EVENT_PGN_SOURCE_ROOT:-$REPO_ROOT/data/generated/chess-results-event-pgn}" \
      "events/chess-results" "objects" "$event_r2_list" "赛事完整 PGN" || return $?
    upload_selected_r2_files \
      "${R2_PGN_SOURCE_ROOT:-$REPO_ROOT/docs/data/pgn}" \
      "data/pgn" "playerObjects" "$player_r2_list" "棋手拆分 PGN" || return $?
  fi
  return "$rc"
}

release_event_data() {
  # Manifest + commit + delivery for cleaned Chess-Results event data.
  # Unchanged events never re-enter the manifest, so an all-fresh queue run
  # commits nothing ("compare says cloud already has it").
  prepare_release event-queue "${EVENT_PATHS[@]}" || return $?
  commit_prepared_release "Release cleaned Chess-Results event data (local manifest)"
}

recover_event_data() {
  local path count=0
  local event_r2_list="$RUN_DIR/diagnostics/r2-event-files.txt"
  local player_r2_list="$RUN_DIR/diagnostics/r2-player-files.txt"
  mkdir -p "$RUN_DIR/diagnostics"
  : > "$event_r2_list"
  : > "$player_r2_list"
  recovery_args=()
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    recovery_args+=(--adopt "$path")
    case "$path" in
      data/generated/chess-results-event-pgn/*)
        printf '%s\n' "${path#data/generated/chess-results-event-pgn/}" >> "$event_r2_list"
        ;;
      docs/data/pgn/*)
        printf '%s\n' "${path#docs/data/pgn/}" >> "$player_r2_list"
        ;;
    esac
    count=$((count + 1))
  done < <(
    py "$RUN_MANAGER" recovery-list --repo "$REPO_ROOT" \
      --allow "${EVENT_PATHS[0]}" --allow "${EVENT_PATHS[1]}" \
      --allow "${EVENT_PATHS[2]}" --allow "${EVENT_PATHS[3]}" \
      --allow "${EVENT_PATHS[4]}" --plain
  )
  if [ "$count" -eq 0 ]; then
    PUSH_SUMMARY="没有需要接管的中断赛事产物"
    return 0
  fi
  state "recovering" "校验并接管 ${count} 个中断机器产物；不访问任何数据源"
  preflight_args=()
  for path in "${EVENT_PATHS[@]}"; do preflight_args+=(--allow "$path"); done
  py "$RUN_MANAGER" preflight --repo "$REPO_ROOT" --run-dir "$RUN_DIR" \
    "${preflight_args[@]}" "${recovery_args[@]}" || return $?
  upload_selected_r2_files \
    "${R2_EVENT_PGN_SOURCE_ROOT:-$REPO_ROOT/data/generated/chess-results-event-pgn}" \
    "events/chess-results" "objects" "$event_r2_list" "恢复赛事完整 PGN" || return $?
  upload_selected_r2_files \
    "${R2_PGN_SOURCE_ROOT:-$REPO_ROOT/docs/data/pgn}" \
    "data/pgn" "playerObjects" "$player_r2_list" "恢复棋手拆分 PGN" || return $?
  release_event_data
  PUSH_SUMMARY="已接管并收口 ${count} 个中断机器产物；发布状态见 outbox"
}

run_private_candidates() {
  ensure_pymod pypinyin
  state "collecting-private" "采集姓名候选；不会写 data/manual 或 data/community"
  export CHINA_CHESS_PRIVATE_EXTRACTED_ROOT="$RUN_DIR/extracted/name-candidates"
  py_extra Scripts/sync_chess_results_starting_rank_aliases.py || return $?
  PUSH_SUMMARY="姓名候选保存在 $RUN_DIR/extracted/name-candidates；等待人工审核"
}

run_lichess() {
  local force="$1"
  preflight_release "${BULK_PATHS[@]}" || return $?
  ensure_pymod zstandard || command -v zstd >/dev/null 2>&1 || fail "DEPENDENCY_INSTALL_FAILED" "缺少 zstandard/zstd。"
  local staging="$RUN_DIR/staging/lichess/docs-data"
  rm -rf "$staging" || return $?
  mkdir -p "$staging/bulk" || return $?
  export CHINA_CHESS_DOCS_DATA_OUTPUT="$staging"
  state "downloading" "镜像 Lichess Broadcast 分片并验证长度、Zstandard 文件签名"
  args=(--metadata-only --mirror --index-youth)
  [ "$force" = "true" ] && args+=(--force)
  py_extra Scripts/sync_lichess_broadcast_bulk.py "${args[@]}" || return $?
  state "promoting" "晋升带 CC BY-SA 4.0 元数据的 Lichess 暂存输出"
  py "$RUN_MANAGER" promote --repo "$REPO_ROOT" --run-dir "$RUN_DIR" \
    --file "$staging/bulk/manifest.json::docs/data/bulk/manifest.json" \
    --file "$staging/bulk/lichess-broadcast/manifest.json::docs/data/bulk/lichess-broadcast/manifest.json" \
    --tree "$staging/bulk/youth::docs/data/bulk/youth" \
    --tree "$staging/bulk/lichess-events::docs/data/bulk/lichess-events" || return $?
  prepare_release bulk "${BULK_PATHS[@]}" || return $?
}

registry_due() {
  python3 - "docs/data/registry/manifest.json" <<'PY'
import datetime as dt, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
try:
    value = json.loads(p.read_text(encoding="utf-8")).get("generatedAt", "")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    age = dt.datetime.now(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)
    raise SystemExit(0 if age.days >= 25 else 1)
except Exception:
    raise SystemExit(0)
PY
}

# --- command router -------------------------------------------------------
case "$command" in
  health)
    state "health-check" "执行本机、缓存、发布路径和来源连通性检查"
    py_extra Scripts/local/health_check.py
    PUSH_SUMMARY="健康检查通过"
    ;;

  registry)
    run_registry
    commit_prepared_release "Release validated FIDE registry (local manifest)" || fail "GIT_PUSH_FAILED" "FIDE 发布提交成功，但推送失败。"
    ;;

  event-queue)
    event_rc=0
    run_private_events || event_rc=$?
    if [ "$event_rc" -ne 0 ] && [ "$event_rc" -ne 4 ]; then
      # Preflight errors already wrote a precise structured error. Exit before
      # finalization so a missing baseline can never turn an older machine
      # output into this run's manifest. Collector failures without a
      # structured error retain the batch-level fallback.
      if [ -s "$RUN_DIR/error.json" ]; then
        exit "$event_rc"
      fi
      fail "CHESS_RESULTS_COLLECTION_FAILED" \
        "${ERROR_MESSAGE:-赛事采集未形成可完成目标；逐场原因见本批结果与 capture-state。}"
    fi
    release_event_data || fail "RELEASE_FINALIZATION_FAILED" \
      "赛事采集结果未能收口为发布包；已完成目标仍保存在本机，请查看结构化错误后重试接管。"
    if [ "$event_rc" -eq 4 ]; then
      partial "PARTIAL_FAILURE" "部分目标需要处理；完整目标、保留的部分数据及实际发布结果见面板“本批结果”。"
    fi
    ;;

  discover-events)
    reject_extra_flags --private-root --max-players --latest-per-player --delay
    state "discovering-events" "按 FIDE ID 查询最近参赛记录；只更新本机 TNR 候选池"
    discovery_rc=0
    py_extra Scripts/local/discover_player_events.py --private-root "$RUN_DIR" || discovery_rc=$?
    if [ "$discovery_rc" -eq 4 ]; then
      partial "PARTIAL_FAILURE" "部分棋手查询失败；成功发现的 TNR 已保留在本机待抓池。"
    elif [ "$discovery_rc" -ne 0 ]; then
      fail "EVENT_DISCOVERY_FAILED" "赛事发现未完成；未触发赛事详情抓取或发布。"
    fi
    PUSH_SUMMARY="赛事发现完成；候选 TNR 已进入本机待抓池，尚未抓取或发布"
    ;;

  recover-events)
    [ "${#EXTRA[@]}" -eq 0 ] || fail "UNSAFE_ARGUMENT_BLOCKED" "recover-events 不接受额外参数。"
    recover_event_data || fail "RECOVERY_RELEASE_FAILED" \
      "中断产物未能安全接管；原文件保持不变，请查看 error.json。"
    ;;

  storage-migrate)
    run_r2_migration
    commit_prepared_release "Release verified R2 PGN migration receipt" \
      || fail "GIT_PUSH_FAILED" "R2 迁移回执提交成功，但推送失败。"
    ;;

  candidates)
    run_private_candidates
    ;;

  bulk)
    run_lichess false
    commit_prepared_release "Release Lichess CC BY-SA broadcast data (local manifest)" || fail "GIT_PUSH_FAILED" "Lichess 发布提交成功，但推送失败。"
    ;;

  bulk-full)
    run_lichess true
    commit_prepared_release "Release refreshed Lichess CC BY-SA broadcast data (local manifest)" || fail "GIT_PUSH_FAILED" "Lichess 发布提交成功，但推送失败。"
    ;;

  all)
    if [ "${#EXTRA[@]}" -gt 0 ]; then
      fail "UNSAFE_ARGUMENT_BLOCKED" "all 是固定安全流程，不接受额外采集参数；请分别运行 registry 或 event-queue。"
    fi
    partial=false
    delivery_pending=false
    if registry_due; then
      step "FIDE registry 已到月度刷新窗口"
      if run_registry; then
        # GitHub delivery failure must never block collection: the release
        # bundle is already committed and saved to the outbox, so we mark it
        # delivery-pending and continue with the event queue.
        if ! commit_prepared_release "Release validated FIDE registry (routine local manifest)"; then
          delivery_pending=true
          DATA_COMMITTED=false
          echo "WARNING: GitHub 投递失败（${LAST_PUSH_ERROR:-GIT_PUSH_FAILED}）；发布包保留在 outbox，继续事件采集，稍后运行 deliver。" >&2
        fi
      else
        partial=true
        echo "WARNING: FIDE 阶段失败；继续执行独立的 Chess-Results 私有队列。" >&2
      fi
    else
      echo "FIDE registry 未到 25 天刷新窗口，本次跳过。"
    fi
    event_rc=0
    run_private_events || event_rc=$?
    if [ "$event_rc" -eq 4 ]; then
      partial=true
      echo "WARNING: Chess-Results 队列部分失败；失败目标已隔离，成功赛事已清洗待发布。" >&2
    elif [ "$event_rc" -ne 0 ]; then
      partial=true
      echo "WARNING: Chess-Results 队列失败，不影响已经完成的 FIDE 发布。" >&2
    fi
    if [ "$event_rc" -eq 0 ] || [ "$event_rc" -eq 4 ]; then
      if ! release_event_data; then
        delivery_pending=true
        DATA_COMMITTED=false
        echo "WARNING: 赛事数据 GitHub 投递失败（${LAST_PUSH_ERROR:-GIT_PUSH_FAILED}）；发布包保留在 outbox，稍后运行 deliver。" >&2
      fi
    fi
    if [ "$partial" = "true" ]; then
      fail "PARTIAL_FAILURE" "部分独立来源失败；成功阶段已保留，失败阶段可单独重试。" 4
    fi
    if [ "$delivery_pending" = "true" ]; then
      PUSH_SUMMARY="采集完成；FIDE 发布包处于 delivery-pending，网络恢复后运行 refresh.sh deliver"
      notify_mac "采集完成；发布待投递（deliver）"
    fi
    ;;

  publish|deliver)
    state "delivering" "推进 GitHub 生产投递与已启用的 Cloudflare 影子回执；不重新访问任何数据源"
    if [ -z "$(py "$RUN_MANAGER" outbox-list --status pending --plain)" ]; then
      # Legacy fallback: HEAD carries a committed manifest from before the
      # outbox existed and it has not been pushed yet. Import it as a bundle.
      if [ -f data/generated/local-release-manifest.json ] && ! cmp -s <(git rev-parse HEAD) "$PUSH_MARKER" 2>/dev/null; then
        py "$RUN_MANAGER" outbox-import --repo "$REPO_ROOT" --commit "$(git rev-parse HEAD)" >/dev/null 2>&1 || true
      fi
    fi
    if [ -z "$(py "$RUN_MANAGER" outbox-list --status pending --retryable-only --plain)" ]; then
      PUSH_SUMMARY="GitHub outbox 没有可自动重试的发布包；需人工关注的包保持隔离"
    else
      deliver_outbox || fail "${LAST_PUSH_ERROR:-GIT_PUSH_FAILED}" \
        "GitHub 传输或认证失败；可重试发布包保留在 outbox。策略冲突包已单独隔离。"
      PUSH_SUMMARY="已投递 ${DELIVERED_COUNT:-0} 个 GitHub 发布包；新增人工关注 ${DELIVERY_ATTENTION_COUNT:-0} 个"
    fi
    shadow_auto_enabled && shadow_retry_existing || true
    if [ -n "$SHADOW_SUMMARY" ]; then
      PUSH_SUMMARY="${PUSH_SUMMARY}；${SHADOW_SUMMARY}"
    fi
    ;;

  receipts)
    state "receipts" "查询云端 ingest/rebuild/deploy 回执并校验线上文件哈希"
    py_extra Scripts/local/check_receipts.py || fail "RECEIPT_CHECK_FAILED" \
      "部分云端回执暂不可读；已确认的阶段不会回退，稍后只重试 receipts。"
    PUSH_SUMMARY="云端回执已同步；线上验证结果见 outbox 状态"
    ;;

  shadow-publish)
    shadow_auto_enabled || fail "CLOUDFLARE_SHADOW_NOT_AUTHORIZED" \
      "Cloudflare 自动影子双写未启用；请先在本机面板明确授权。"
    state "shadow-delivering" "仅推进已授权的 Cloudflare 影子回执；不访问来源、不触碰 GitHub"
    shadow_retry_existing || fail "CLOUDFLARE_SHADOW_AUTO_PAUSED" \
      "影子端点不可用或状态不确定，自动影子双写已暂停；GitHub 生产不受影响。"
    PUSH_SUMMARY="${SHADOW_SUMMARY:-Cloudflare 影子没有待推进回执}"
    ;;

  shadow-deliver)
    [ "${#EXTRA[@]}" -eq 1 ] || fail "UNSAFE_ARGUMENT_BLOCKED" \
      "shadow-deliver 只接受一个现有 outbox run-id；不会访问任何数据源。"
    state "shadow-delivering" "将现有不可变 outbox 双写到 Cloudflare 免费层影子 ingest"
    py Scripts/local/cloudflare_ingest.py --run-id "${EXTRA[0]}" \
      || fail "CLOUDFLARE_SHADOW_DELIVERY_FAILED" \
        "影子投递未完成；GitHub 生产链路和本机 outbox 未改变。"
    PUSH_SUMMARY="Cloudflare 影子发布完成；生产仍使用 GitHub 链路"
    ;;

  reindex)
    state "offline-diagnostic" "本地全快照离线重建；不提交、不推送"
    py Scripts/build_release_snapshot.py
    PUSH_SUMMARY="离线诊断重建完成；工作区改动未自动提交"
    ;;

  crawl|crawl-full|pgn|pgn-full|events|events-full|aliases|promote|reconcile|verify|contrib)
    fail "COMPLIANCE_POLICY_BLOCKED" \
      "命令 $command 已退役：Chess-Results 采集与发布只走 event-queue/candidates，社区载荷不得包含抓取结果。" 3
    ;;

  *)
    fail "UNKNOWN_COMMAND" "未知命令：${command}；运行 Scripts/local/refresh.sh help 查看安全命令。" 2
    ;;
esac
