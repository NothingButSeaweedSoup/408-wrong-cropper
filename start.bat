@echo off
REM 一键启动后端（用户端 H5 在 /，管理后台在 /admin/，端口见 backend/config.py 的 SERVER_PORT）。
REM 管理密钥在 backend/.env，首次启动会自动生成并打印在控制台。
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m backend
pause
