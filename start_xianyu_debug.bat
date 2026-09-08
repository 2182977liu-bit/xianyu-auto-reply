@echo off
REM ============================================================
REM  闲鱼卖家客服 - 调试模式启动脚本
REM  作用：关闭现有实例，并用 --remote-debugging-port=9222
REM        重新启动，以便自动化程序通过 CDP 控制它
REM ============================================================
chcp 65001 >nul

echo [1/2] 正在关闭已运行的 闲鱼卖家客服 ...
taskkill /IM "闲鱼卖家客服.exe" /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/2] 正在以调试模式启动 闲鱼卖家客服 ...
start "" "D:\Program Files\xianyu-seller-im\闲鱼卖家客服.exe" --remote-debugging-port=9222 --remote-allow-origins=*

echo.
echo 启动完成！请等待软件窗口完全加载。
echo 验证调试端口是否就绪：
echo   http://127.0.0.1:9222/json
pause
