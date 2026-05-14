@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul 2>&1
title SA Runner ^— Stopping...

:: ─────────────────────────────────────────────────────────────────────────────
:: SA Runner Stop Script
:: Finds the uvicorn process bound to port 8765 and terminates it.
:: ─────────────────────────────────────────────────────────────────────────────

set "PORT=8765"

echo.
echo [SA Runner] Stopping server on port !PORT! ...

:: Find the PID listening on port 8765
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr /C:":!PORT! " ^| findstr "LISTENING"') do (
    if not defined PORT_PID set "PORT_PID=%%P"
)

if not defined PORT_PID (
    echo [SA Runner] No process found listening on port !PORT! — already stopped.
    goto DONE
)

echo [SA Runner] Found server process PID !PORT_PID! — terminating...
taskkill /PID !PORT_PID! /F > nul 2>&1
if !errorlevel! == 0 (
    echo [SA Runner] Server stopped ^(PID !PORT_PID!^).
) else (
    echo [SA Runner] WARNING: taskkill returned a non-zero exit code for PID !PORT_PID!.
    echo [SA Runner] The process may have already exited, or you may need to stop it manually.
)

:DONE
echo.
exit /b 0
