#!/usr/bin/env bash
# RADAR 部署助手启动器（macOS / Linux 上双击或 ./deploy_gui.command）
# 优先使用项目自带的 .venv，没有则回退到系统 python3。
cd "$(dirname "$0")/../.." || exit 1

PY="python3"
if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
elif ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3.10 或更高版本。"
  read -r -p "按回车键关闭…" _
  exit 1
fi

exec "$PY" webapp/deploy/deploy_gui.py
