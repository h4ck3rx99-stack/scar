@echo off
title Scar - First Time Setup
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo           Scar Desktop Assistant - Initial Setup
echo ============================================================
echo.

rem --- 1. Check Python ---
echo [1/5] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    py --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Python was not found on your system!
        echo Please download and install Python (3.10, 3.11, or 3.12) from:
        echo https://www.python.org/downloads/
        echo.
        echo IMPORTANT: Check the box "Add Python to PATH" during installation.
        echo After installing Python, run this setup script again.
        echo.
        pause
        exit /b 1
    )
)
echo     - Python is installed.

rem --- 2. Check Node.js and npm ---
echo.
echo [2/5] Checking Node.js and npm...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Node.js was not found on your system!
    echo Please download and install Node.js (LTS version) from:
    echo https://nodejs.org/
    echo After installing Node.js, run this setup script again.
    echo.
    pause
    exit /b 1
)
npm --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] npm was not found. Please reinstall Node.js with default tools.
    pause
    exit /b 1
)
echo     - Node.js and npm are installed.

rem --- 3. Check / Install llama-server ---
echo.
echo [3/5] Checking llama-server (Local AI Engine)...
where llama-server.exe >nul 2>&1
if %errorlevel% neq 0 (
    echo     - llama-server not found on PATH. Attempting automatic install via winget...
    winget --version >nul 2>&1
    if %errorlevel% equ 0 (
        echo     - Running: winget install ggml.llamacpp ...
        winget install ggml.llamacpp --accept-source-agreements --accept-package-agreements
        if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links" (
            set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;!PATH!"
        )
    ) else (
        echo     [!] Note: If winget is not available, install llama.cpp manually or run:
        echo     winget install ggml.llamacpp
    )
) else (
    echo     - llama-server is ready.
)

rem --- 4. Setup Python Virtual Environment ---
echo.
echo [4/5] Setting up Python virtual environment (backend\.venv)...
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
    echo     - Creating virtual environment...
    python -m venv .venv 2>nul || py -3 -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Failed to create Python virtual environment in backend\.venv
    echo Make sure Python is properly installed and accessible from terminal.
    pause
    exit /b 1
)

echo     - Installing Python dependencies (this may take a few minutes)...
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
call .venv\Scripts\pip.exe install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Some dependencies had warnings during install.
)

rem --- 5. Setup Frontend & Build ---
echo.
echo [5/5] Installing Frontend dependencies and building UI...
cd /d "%~dp0frontend"
call npm install
echo     - Building UI bundle...
call npx vite build --logLevel error

rem --- Check Local Models ---
cd /d "%~dp0"
echo.
if exist "backend\models\llm\Qwen3.5-4B-Q4_K_M.gguf" (
    echo [OK] Local AI models are present and ready.
) else (
    echo [!] Note: backend\models directory does not contain the complete model weights.
)

rem --- Done ---
echo.
echo ============================================================
echo                    SETUP COMPLETED!
echo ============================================================
echo.
echo You are ready to start Scar!
echo Just double-click "Start_Scar.bat" to launch your assistant.
echo.
pause
