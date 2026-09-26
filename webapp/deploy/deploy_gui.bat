@echo off
rem RADAR 部署助手启动器（双击运行）
rem 优先使用项目自带的 .venv，没有则回退到系统 Python。
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo 未找到 Python。请先安装 Python 3.10 或更高版本，并勾选 "Add to PATH"。
  echo.
  pause
  exit /b 1
)

"%PY%" "webapp\deploy\deploy_gui.py"
if errorlevel 1 (
  echo.
  echo 程序异常退出，请把上方信息截图反馈。
  pause
)
