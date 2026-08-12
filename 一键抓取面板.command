#!/bin/bash
# 一键抓取控制面板 — 双击启动(Terminal 版,不受 .app 签名/隔离限制)。
# 与「一键抓取.app」等价:启动本地面板并在浏览器打开;此窗口显示面板日志,
# 关闭窗口前请先用页面里的「退出面板」。
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
launcher_root="$(cd "$(dirname "$0")" && pwd)"
role="$(git -C "$launcher_root" config --get chessdb.workspaceRole 2>/dev/null || true)"
if [ "$role" = "code" ]; then
  collector_root="$(git -C "$launcher_root" config --get chessdb.collectorRoot 2>/dev/null || true)"
  if [ -z "$collector_root" ] || [ ! -d "$collector_root" ]; then
    echo "WRONG_WORKSPACE_ROLE: 当前是 code 工作区，但未找到 collectorRoot。"
    read -r -p "按回车退出…" _
    exit 2
  fi
  launcher_root="$collector_root"
fi
role="$(git -C "$launcher_root" config --get chessdb.workspaceRole 2>/dev/null || true)"
if [ "$role" != "collector" ]; then
  echo "WRONG_WORKSPACE_ROLE: 采集面板只能从 collector 工作区启动；当前角色为 ${role:-unset}。"
  read -r -p "按回车退出…" _
  exit 2
fi
cd "$launcher_root" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3。请先安装 Xcode 命令行工具:xcode-select --install"
  read -r -p "按回车退出…" _
  exit 1
fi
exec python3 Scripts/local/panel.py
