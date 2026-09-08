@echo off
REM ============================================================
REM  配置中心启动脚本
REM  运行后自动打开浏览器：http://127.0.0.1:5000
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
start "" "http://127.0.0.1:5000"
..\venv\Scripts\python.exe config_ui\app.py
echo.
echo 配置中心已停止。
pause
