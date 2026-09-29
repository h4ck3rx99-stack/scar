@echo off
title Scar
setlocal
cd /d "%~dp0"

echo.
echo   ===============================
echo      Starting Scar
echo   ===============================
echo.

rem --- stop an earlier copy (only Scar's own processes: its Electron, Python brain and llama-server)
call "%~dp0Stop_Scar.bat" quiet

rem --- sanity checks
if not exist "backend\.venv\Scripts\python.exe" (
  echo   [!] Python environment missing: backend\.venv
  echo       Ask Claude to re-run the backend setup.
  pause
  exit /b 1
)
if not exist "frontend\node_modules\electron\dist\electron.exe" (
  echo   [!] Electron missing: frontend\node_modules\electron
  pause
  exit /b 1
)

rem --- build the UI (fast; keeps it in sync with the source)
pushd frontend
call npx vite build --logLevel error
if errorlevel 1 (
  echo   [!] UI build failed; starting the last good build.
)
popd

rem --- launch: Electron shows the robot and starts the Python brain itself
start "" /d "%~dp0frontend" "%~dp0frontend\node_modules\electron\dist\electron.exe" "electron\main.cjs"

echo   Scar is starting (the models take ~10 seconds to load).
echo   Say "Scar" or "Hey Scar" to talk. Quit from the tray icon or run Stop_Scar.bat.
timeout /t 4 /nobreak >nul
exit /b 0
