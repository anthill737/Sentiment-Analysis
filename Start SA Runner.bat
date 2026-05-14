@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul 2>&1
title SA Runner ^— Starting...

:: ─────────────────────────────────────────────────────────────────────────────
:: SA Runner Launcher
:: Self-bootstraps the Bundled Runtime on first run, then starts the server.
:: ─────────────────────────────────────────────────────────────────────────────

:: Project root is the directory containing this .bat file
set "SCRIPT_DIR=%~dp0"
:: Strip trailing backslash for consistent path concatenation
set "SCRIPT_DIR=!SCRIPT_DIR:~0,-1!"

:: Computed paths
set "SARUNNER_HOME=%LOCALAPPDATA%\sa-runner"
set "VENV_DIR=!SARUNNER_HOME!\venv"
set "PORT=8770"
set "SERVER_URL=http://127.0.0.1:!PORT!"

echo.
echo ╔══════════════════════════════╗
echo ║       SA Runner              ║
echo ╚══════════════════════════════╝
echo.

:: ─── Ensure .env exists with APP_PASSWORD ────────────────────────────────────
if not exist "!SCRIPT_DIR!\.env" (
    echo [SA Runner] First launch — creating .env file.
    echo.
    set /p "_pass=  Enter an App Password for SA Runner: "
    if "!_pass!"=="" (
        echo.
        echo [SA Runner] ERROR: App Password cannot be empty.
        pause
        exit /b 1
    )
    echo APP_PASSWORD=!_pass!> "!SCRIPT_DIR!\.env"
    echo.
    echo [SA Runner] .env created at !SCRIPT_DIR!\.env
    echo.
)

:: ─── Bootstrap Bundled Runtime (downloads + verifies on first run) ────────────
echo [SA Runner] Checking Bundled Runtime...
powershell.exe -NoProfile -ExecutionPolicy Bypass ^
    -File "!SCRIPT_DIR!\scripts\bootstrap.ps1" ^
    -ScriptDir "!SCRIPT_DIR!"
if !errorlevel! neq 0 (
    echo.
    echo [SA Runner] Bootstrap failed. Review the messages above for details.
    pause
    exit /b 1
)

:: ─── Check whether port 8770 is already bound ────────────────────────────────
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr /C:":!PORT! " ^| findstr "LISTENING"') do (
    if not defined PORT_PID set "PORT_PID=%%P"
)
if defined PORT_PID (
    echo.
    echo [SA Runner] ERROR: Port !PORT! is already bound by PID !PORT_PID!.
    echo [SA Runner] Run "Stop SA Runner.bat" to stop a previous instance,
    echo [SA Runner] or terminate PID !PORT_PID! manually, then try again.
    pause
    exit /b 1
)

:: ─── Start the FastAPI server in a separate window ───────────────────────────
echo.
echo [SA Runner] Starting server on !SERVER_URL! ...
start "SA Runner ^— Server" /D "!SCRIPT_DIR!" ^
    "!VENV_DIR!\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port !PORT!

:: ─── Poll until server is ready (no connection-refused race condition) ────────
echo [SA Runner] Waiting for server to become ready...
set /a POLL_COUNT=0

:POLL_LOOP
timeout /t 1 /nobreak > nul
powershell.exe -NoProfile -Command ^
    "try { Invoke-WebRequest -Uri '!SERVER_URL!/' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop | Out-Null; Write-Host 'SA_READY' } catch { }" ^
    > "!TEMP!\sa_runner_poll.tmp" 2>&1
findstr /C:"SA_READY" "!TEMP!\sa_runner_poll.tmp" > nul 2>&1
del "!TEMP!\sa_runner_poll.tmp" > nul 2>&1
if !errorlevel! == 0 goto OPEN_BROWSER
set /a POLL_COUNT+=1
if !POLL_COUNT! lss 30 goto POLL_LOOP

echo.
echo [SA Runner] ERROR: Server did not become ready within 30 seconds.
echo [SA Runner] Check the "SA Runner ^— Server" window for error messages.
pause
exit /b 1

:OPEN_BROWSER
echo [SA Runner] Server is ready. Opening browser...
start "" !SERVER_URL!

echo.
echo [SA Runner] SA Runner is running at !SERVER_URL!
echo [SA Runner] To stop: close the "SA Runner ^— Server" window
echo [SA Runner]          or run "Stop SA Runner.bat".
echo.
exit /b 0
