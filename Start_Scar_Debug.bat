@echo off
title Scar (debug log)
cd /d "%~dp0"
rem Same as Start_Scar.bat, but the brain runs in THIS window so you can watch what it hears and does.
call "%~dp0Stop_Scar.bat" quiet
pushd frontend
call npx vite build --logLevel error
popd
rem Electron starts a few seconds later, finds this brain already running, and connects to it.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start "" /d "%~dp0frontend" "%~dp0frontend\node_modules\electron\dist\electron.exe" electron\main.cjs"
cd backend
".venv\Scripts\python.exe" -m jarvis
pause
