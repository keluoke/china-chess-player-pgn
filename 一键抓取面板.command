#!/bin/bash
# 一键抓取控制面板 — 双击启动(Terminal 版,不受 .app 签名/隔离限制)。
# 与「一键抓取.app」等价:启动本地面板并在浏览器打开;此窗口显示面板日志,
# 关闭窗口前请先用页面里的「退出面板」。
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3。请先安装 Xcode 命令行工具:xcode-select --install"
  read -r -p "按回车退出…" _
  exit 1
fi
exec python3 Scripts/local/panel.py
