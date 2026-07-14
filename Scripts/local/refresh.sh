#!/usr/bin/env bash
#
# Maintainer-local data collection entrypoint.
#
# Collection happens only on this workstation.  Chess-Results is link-only by
# default and writes raw/parsed data into a private per-run directory outside
# the repository.  FIDE and Lichess releases are staged, validated, promoted,
# listed in an exact manifest, committed locally and force-pushed to the
# single-writer local-data branch.  GitHub never scrapes a source.
#
# Usage: Scripts/local/refresh.sh <command> [--no-push] [-- <extra args>]
#
# Safe commands:
#   health       Workstation, cache, worktree and provider connectivity checks.
#   all          Safe routine: monthly-due FIDE registry + top 3 private events.
#   registry     Download/validate FIDE and release the registry projection.
#   event-queue  Collect top 3 targets privately (or pass explicit queue args).
#   candidates   Collect starting-rank name candidates privately for review.
#   bulk         Mirror Lichess Broadcasts under CC BY-SA 4.0 and release.
#   bulk-full    Same as bulk, force-refresh every selected shard.
#   deliver      Deliver pending outbox release bundles; never re-scrapes.
#   push         Alias of deliver (kept for compatibility).
#   receipts     Sync cloud ingest/rebuild/deploy receipts + online check.
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

py() { python3 -u "$@"; }
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
# This safe entrypoint is permanently link-only. Exceptional publication
# authorization must use a separate audited workflow; inherited environment
# variables cannot silently widen the panel's behavior.
export CHESS_RESULTS_RELEASE_POLICY=link-only
unset CHESS_RESULTS_PUBLICATION_AUTHORIZED || true

BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "${BOLD}${CYAN}" "$*" "$RESET"; }

notify_mac() {
  command -v osascript >/dev/null 2>&1 || return 0
  osascript -e "display notification \"$1\" with title \"棋手数据刷新\"" >/dev/null 2>&1 || true
}

ERROR_CODE=""
ERROR_MESSAGE=""
PUSH_SUMMARY=""
DATA_COMMITTED=false
DELIVERED_COUNT=0
RUN_DIR=""

RUN_DIR="$(py "$RUN_MANAGER" acquire --command "$command" --pid "$$")" || exit $?
RUN_LOG="$RUN_DIR/run.log"
touch "$RUN_LOG"
exec > >(tee -a "$RUN_LOG") 2>&1

state() {
  py "$RUN_MANAGER" update --run-dir "$RUN_DIR" --stage "$1" --message "$2" >/dev/null
}

fail() {
  ERROR_CODE="$1"
  ERROR_MESSAGE="$2"
  printf '%s%s: %s%s\n' "$RED" "$ERROR_CODE" "$ERROR_MESSAGE" "$RESET" >&2
  exit "${3:-1}"
}

on_signal() {
  ERROR_CODE="INTERRUPTED"
  ERROR_MESSAGE="任务已由用户中止；未完成的暂存运行不会发布。"
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
    # Mixed batch (exit 4, PARTIAL_FAILURE): keep the aggregate code — the
    # last underlying per-target error in the log must not masquerade as the
    # whole batch's outcome. Per-target codes live in result.json/capture-state.
    if [ "$status" -eq 4 ] && [ "$ERROR_CODE" = "PARTIAL_FAILURE" ]; then
      detected=""
    else
      detected="$(tail -n 120 "$RUN_LOG" 2>/dev/null | grep -Eo 'LOCAL_MAINTAINER_ACK_REQUIRED|COMPLIANCE_POLICY_BLOCKED|SOURCE_CIRCUIT_OPEN|VISIT_BUDGET_EXHAUSTED|SOURCE_BLOCKED_OR_RATE_LIMITED|SOURCE_TRUNCATED_DOWNLOAD|SOURCE_FILE_SIGNATURE_INVALID|SOURCE_UNEXPECTED_CONTENT_TYPE|SOURCE_NETWORK_FAILURE|EVENT_EMPTY|PAIRINGS_NOT_PUBLISHED|TEAM_FORMAT_UNSUPPORTED|ROUND_COUNT_UNKNOWN|PAIRING_REFS_OUTSIDE_ROSTER|PAGE_CACHE_MISS|PARSER_LAYOUT_CHANGED|VALIDATION_REGRESSION|REGISTRY_AUTHORITY_MISMATCH|NAME_CORRECTION_REGRESSION|DIRTY_RELEASE_PATH|GIT_INDEX_NOT_CLEAN|WORKTREE_CHANGED_DURING_RUN|RELEASE_HASH_MISMATCH|GIT_DNS_FAILURE|GIT_TLS_FAILURE|GIT_PROXY_FAILURE|GIT_AUTH_FAILED|GIT_REMOTE_REJECTED|GIT_CONNECT_FAILURE' | tail -1)"
    fi
    [ -n "$detected" ] && ERROR_CODE="$detected"
    if [ "$DATA_COMMITTED" = "true" ]; then
      result="push-failed"
      ERROR_CODE="${ERROR_CODE:-GIT_PUSH_FAILED}"
      message="数据已按 manifest 提交在本地，推送失败；使用 push 重投即可。"
    else
      result="failed"
      ERROR_CODE="${ERROR_CODE:-UNEXPECTED_FAILURE}"
      message="${ERROR_MESSAGE:-任务失败，请查看本次运行日志。}"
    fi
  fi
  py "$RUN_MANAGER" finish --run-dir "$RUN_DIR" --code "$status" \
    --result "$result" --error-code "$ERROR_CODE" --message "$message" >/dev/null 2>&1
  if [ "$status" -eq 0 ]; then
    printf '\n%s✅ 完成：%s %s%s\n' "$GREEN" "$command" "$PUSH_SUMMARY" "$RESET"
    notify_mac "完成：$command ✅"
  elif [ "$DATA_COMMITTED" = "true" ]; then
    printf '\n%s⚠️ 数据已提交本地，推送失败；稍后运行 push。%s\n' "$RED" "$RESET"
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
  local mod="$1" pkg="${2:-$1}" args
  python3 -c "import $mod" 2>/dev/null && return 0
  step "安装 Python 依赖：$pkg"
  for args in "" "--break-system-packages" \
              "-i https://pypi.tuna.tsinghua.edu.cn/simple" \
              "-i https://pypi.tuna.tsinghua.edu.cn/simple --break-system-packages"; do
    # shellcheck disable=SC2086
    if python3 -m pip install --user --quiet --disable-pip-version-check --default-timeout=20 $args "$pkg" 2>/dev/null \
       && python3 -c "import $mod" 2>/dev/null; then
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
  scutil --proxies 2>/dev/null | awk '
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
  if [ -n "$proxy" ]; then
    git -c http.proxy="$proxy" -c https.proxy="$proxy" "$@"
  else
    git "$@"
  fi
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

push_commit_with_routes() {
  # Push an exact, immutable commit SHA to the single-writer branch, rotating
  # to the next route on failure instead of retrying the same dead route.
  local sha="$1" route err classification
  LAST_PUSH_ERROR=""
  while IFS= read -r route; do
    if ! github_ok "$route"; then
      echo "GitHub 路线 ${route:-direct} smart HTTP 不可达，跳过。" >&2
      continue
    fi
    [ -n "$route" ] && echo "GitHub 使用路线 ${route}；数据来源仍保持住宅 IP 直连。"
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

api_fallback_deliver() {
  # Same manifest, same hashed files: the API path reuses the outbox bundle
  # and run_manager.validate_manifest; it is not a second policy.
  local run_id="$1"
  command -v gh >/dev/null 2>&1 || return 1
  gh auth status >/dev/null 2>&1 || return 1
  echo "Git 路线全部失败；尝试 GitHub Git Database API 兜底投递 ${run_id}。"
  py Scripts/local/publish_data_via_api.py --outbox "$run_id"
}

deliver_outbox() {
  # Deliver every pending release bundle, oldest first. Never re-scrapes.
  local line run_id sha delivered=0 failed=0
  while IFS=$'\t' read -r run_id sha; do
    [ -n "$run_id" ] || continue
    echo "投递 outbox release ${run_id}（commit ${sha}）"
    if push_commit_with_routes "$sha"; then
      py "$RUN_MANAGER" outbox-update --run-id "$run_id" --status pushed \
        --remote-sha "$sha" --route git >/dev/null
      delivered=$((delivered + 1))
    elif api_fallback_deliver "$run_id"; then
      delivered=$((delivered + 1))
    else
      py "$RUN_MANAGER" outbox-update --run-id "$run_id" --status pending \
        --error "${LAST_PUSH_ERROR:-GIT_PUSH_FAILED}" >/dev/null || true
      failed=$((failed + 1))
      # Deterministic auth/policy failures affect every bundle equally.
      case "$LAST_PUSH_ERROR" in GIT_AUTH_FAILED|GIT_REMOTE_REJECTED) break ;; esac
    fi
  done < <(py "$RUN_MANAGER" outbox-list --status pending --plain)
  DELIVERED_COUNT="$delivered"
  [ "$failed" -eq 0 ]
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
  if [ "$PUSH" = "true" ]; then
    state "delivering" "投递 outbox 发布包到单写者 local-data 分支"
    deliver_outbox || return 1
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
  ERROR_CODE="RELEASE_VALIDATION_FAILED"
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
  ERROR_CODE="DIRTY_RELEASE_PATH"
  py "$RUN_MANAGER" preflight --repo "$REPO_ROOT" --run-dir "$RUN_DIR" "${args[@]}"
}

# --- isolated collectors --------------------------------------------------
REGISTRY_PATHS=(
  "docs/data/registry"
  "data/generated/federation-snapshots"
  "data/generated/transfer-candidates.json"
)
BULK_PATHS=("docs/data/bulk")

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
  ERROR_CODE="FIDE_DOWNLOAD_OR_VALIDATION_FAILED"
  py_extra Scripts/sync_chinese_players.py \
    --output-root "$staging/registry" \
    --snapshot-dir "$staging/generated/federation-snapshots" \
    --transfer-candidates "$staging/generated/transfer-candidates.json" || return $?
  state "validating" "校验 registry 权威字段、分片一致性和姓名勘误"
  ERROR_CODE="VALIDATION_REGRESSION"
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

run_private_events() {
  # Exit code 4 = partial batch: some targets failed but were isolated and
  # recorded; completed targets are checkpointed and never re-scraped.
  reject_extra_flags --private-root --authorized-publication
  state "collecting-private" "按社区线索队列采集 Chess-Results；原始与解析数据仅写本地私有区"
  ERROR_CODE="CHESS_RESULTS_PRIVATE_COLLECTION_FAILED"
  local rc=0
  if [ "${#EXTRA[@]}" -eq 0 ]; then
    py Scripts/sync_chess_results_event.py --from-queue 3 --private-root "$RUN_DIR" || rc=$?
  else
    py_extra Scripts/sync_chess_results_event.py --private-root "$RUN_DIR" || rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    PUSH_SUMMARY="私有采集完成；未向仓库发布 Chess-Results 内容"
  elif [ "$rc" -eq 4 ]; then
    PUSH_SUMMARY="私有采集部分完成；失败目标已隔离记录，成功赛事已检查点保存"
  else
    # All targets failed. The collector already printed the structured code
    # (e.g. EVENT_EMPTY) which on_exit extracts from the log; give the user a
    # concrete pointer instead of a generic failure line.
    ERROR_MESSAGE="全部目标失败；结构化原因、失败页面与原始证据已写入 capture-state 和本次 raw/ 目录，来源记录不存在的目标已自动隔离。"
  fi
  return "$rc"
}

run_private_candidates() {
  ensure_pymod pypinyin
  state "collecting-private" "采集姓名候选；不会写 data/manual 或 data/community"
  ERROR_CODE="CHESS_RESULTS_PRIVATE_COLLECTION_FAILED"
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
  ERROR_CODE="LICHESS_DOWNLOAD_OR_VALIDATION_FAILED"
  args=(--metadata-only --mirror --index-youth)
  [ "$force" = "true" ] && args+=(--force)
  py_extra Scripts/sync_lichess_broadcast_bulk.py "${args[@]}" || return $?
  state "promoting" "晋升带 CC BY-SA 4.0 元数据的 Lichess 暂存输出"
  py "$RUN_MANAGER" promote --repo "$REPO_ROOT" --run-dir "$RUN_DIR" \
    --overlay "$staging/bulk::docs/data/bulk" || return $?
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
    ERROR_CODE="HEALTH_CHECK_FAILED"
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
    if [ "$event_rc" -eq 4 ]; then
      fail "PARTIAL_FAILURE" "部分赛事目标失败已隔离；成功赛事保留，失败原因见 capture-state 与本次日志。" 4
    elif [ "$event_rc" -ne 0 ]; then
      exit "$event_rc"
    fi
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
      echo "WARNING: Chess-Results 私有队列部分失败；失败目标已隔离，成功赛事已保留。" >&2
    elif [ "$event_rc" -ne 0 ]; then
      partial=true
      echo "WARNING: Chess-Results 私有队列失败，不影响已经完成的 FIDE 发布。" >&2
    fi
    if [ "$partial" = "true" ]; then
      fail "PARTIAL_FAILURE" "部分独立来源失败；成功阶段已保留，失败阶段可单独重试。" 4
    fi
    if [ "$delivery_pending" = "true" ]; then
      PUSH_SUMMARY="采集完成；FIDE 发布包处于 delivery-pending，网络恢复后运行 refresh.sh deliver"
      notify_mac "采集完成；发布待投递（deliver）"
    fi
    ;;

  deliver|push|redeliver)
    state "delivering" "投递 outbox 中待发布的 release 包；不重新访问任何数据源"
    if [ -z "$(py "$RUN_MANAGER" outbox-list --status pending --plain)" ]; then
      # Legacy fallback: HEAD carries a committed manifest from before the
      # outbox existed and it has not been pushed yet. Import it as a bundle.
      if [ -f data/generated/local-release-manifest.json ] && ! cmp -s <(git rev-parse HEAD) "$PUSH_MARKER" 2>/dev/null; then
        py "$RUN_MANAGER" outbox-import --repo "$REPO_ROOT" --commit "$(git rev-parse HEAD)" >/dev/null 2>&1 || true
      fi
    fi
    if [ -z "$(py "$RUN_MANAGER" outbox-list --status pending --plain)" ]; then
      PUSH_SUMMARY="outbox 没有待投递发布包"
    else
      deliver_outbox || fail "${LAST_PUSH_ERROR:-GIT_PUSH_FAILED}" "所有 GitHub 路线均不可用；发布包保留在 outbox，网络恢复后重试 deliver。"
      PUSH_SUMMARY="已投递 ${DELIVERED_COUNT:-0} 个发布包"
      # Best-effort receipt sync: pushed is not published. Failures here never
      # affect the delivery result.
      py Scripts/local/check_receipts.py || true
    fi
    ;;

  receipts)
    state "receipts" "查询云端 ingest/rebuild/deploy 回执并校验线上文件哈希"
    ERROR_CODE="RECEIPT_CHECK_FAILED"
    py_extra Scripts/local/check_receipts.py
    PUSH_SUMMARY="云端回执已同步；线上验证结果见 outbox 状态"
    ;;

  reindex)
    state "offline-diagnostic" "本地离线重建；不提交、不推送"
    py Scripts/sync_domestic_players.py
    [ -f Scripts/build_domestic_progressions.py ] && py Scripts/build_domestic_progressions.py
    py Scripts/sync_static_pgn.py
    py Scripts/build_static_player_pgn.py
    [ -f Scripts/build_event_details.py ] && py Scripts/build_event_details.py
    [ -f Scripts/build_event_catalog.py ] && py Scripts/build_event_catalog.py
    [ -f Scripts/build_data_quality_audit.py ] && py Scripts/build_data_quality_audit.py
    PUSH_SUMMARY="离线诊断重建完成；工作区改动未自动提交"
    ;;

  crawl|crawl-full|pgn|pgn-full|events|events-full|aliases|promote|reconcile|verify|contrib)
    fail "COMPLIANCE_POLICY_BLOCKED" \
      "命令 $command 已退役：Chess-Results 只允许 event-queue/candidates 私有采集，社区载荷不得包含抓取结果。" 3
    ;;

  *)
    fail "UNKNOWN_COMMAND" "未知命令：${command}；运行 Scripts/local/refresh.sh help 查看安全命令。" 2
    ;;
esac
