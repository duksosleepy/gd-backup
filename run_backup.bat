@echo off
setlocal
echo ========================================================
echo   Starting GD Backup Service (Direct uv execution)
echo ========================================================
call uv run backup.py
pause
