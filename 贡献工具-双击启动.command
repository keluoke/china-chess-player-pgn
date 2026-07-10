#!/bin/bash
# 中国国际象棋数据库 · 社区贡献工具(macOS 双击启动)
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3。请先安装 Xcode 命令行工具:xcode-select --install"
  read -r -p "按回车退出…" _
  exit 1
fi
exec python3 Scripts/contrib/contrib_tool.py
