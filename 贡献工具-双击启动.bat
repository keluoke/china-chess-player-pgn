@echo off
rem 中国国际象棋数据库 · 社区贡献工具(Windows 双击启动)
cd /d "%~dp0"
where py >nul 2>nul && (py -3 Scripts\contrib\contrib_tool.py & goto :eof)
where python >nul 2>nul && (python Scripts\contrib\contrib_tool.py & goto :eof)
echo 未找到 Python。请到 https://www.python.org/downloads/ 安装(勾选 Add to PATH)后重试。
pause
