@echo off
title Jarvis 3D Desktop Assistant
echo ====================================================
echo     Launching Jarvis 3D Roaming Desktop Assistant
echo ====================================================
echo.
cd /d "%~dp0frontend"

:: Terminate any stale electron processes before launch to prevent duplicates or audio leaks
taskkill /F /IM electron.exe >nul 2>&1

:: Check if server is running on 5173
netstat -ano | findstr :5173 >nul
if %errorlevel% neq 0 (
    echo Starting Assistant Engine in background...
    start /b npx vite --port 5173 --host >nul 2>&1
    timeout /t 2 /nobreak >nul
)

echo Activating Transparent Desktop Overlay Window...
npx electron electron/main.cjs
