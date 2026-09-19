@echo off
REM 一键启动后端：先打印本机局域网 IP（手机 / 安卓 App 要填的地址），再启动服务。
REM 端口见 backend/config.py 的 SERVER_PORT（默认 18100）；管理密钥见 backend/.env。
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 没找到 .venv，请先按 README 第 3 节创建虚拟环境并安装依赖。
  pause
  exit /b 1
)

echo ================================================================
".venv\Scripts\python.exe" -m backend --print-ip
echo ================================================================
echo 正在启动服务（Ctrl+C 停止）...
echo.

".venv\Scripts\python.exe" -m backend
pause
