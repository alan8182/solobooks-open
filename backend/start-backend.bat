@echo off
setlocal
cd /d "%~dp0"
title SoloBooks Backend
echo Starting http://127.0.0.1:8000 - Press Ctrl+C to stop server
echo Stop old uvicorn on this machine if still running...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'uvicorn\s+main:app' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000

echo.
echo Server stopped. Press any key to close this window.
pause >nul
