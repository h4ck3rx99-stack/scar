@echo off
title Jarvis
cd /d "%~dp0frontend"
rem Build the UI once if it has never been built.
if not exist "dist\index.html" call npx vite build
rem Electron starts the Python brain (backend\.venv) itself and stops it on quit.
start "" npx electron electron/main.cjs
