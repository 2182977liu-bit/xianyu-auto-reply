@echo off
REM ============================================================
REM  闲鱼自动回复机器人 启动脚本
REM  无参数       = 试运行（只检测不回发，安全）
REM  --no-dry-run = 正式自动回复
REM  --mode rule|mixed|ai 切换生成策略
REM  例：start_bot.bat --no-dry-run
REM  例：start_bot.bat --no-dry-run --mode ai
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
..\venv\Scripts\python.exe bot.py %*
echo.
echo 机器人已退出。
pause