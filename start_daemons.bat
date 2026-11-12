@echo off
setlocal
echo ========================================================
echo   Starting GD Backup Daemons (Windows)
echo ========================================================
call mise daemons start
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start daemons via mise.
) else (
    echo [SUCCESS] Daemons started.
    echo Backend:  http://localhost:8100
    echo Frontend: http://localhost:4173
)
pause
