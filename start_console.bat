@echo off
rem  Launch the 3DGS web console (double-click to start).
rem  ASCII-only comments on purpose: cmd.exe defaults to GBK, UTF-8 shows as mojibake.

setlocal
cd /d "%~dp0"

rem  Find a usable python. `py` launcher is broken on some machines, so try python first.
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY if exist "D:\python\3.12.8\python.exe" set "PY=D:\python\3.12.8\python.exe"
if not defined PY (
    echo [ERROR] python not found on PATH.
    echo         Install Python or edit this file to point at python.exe.
    pause
    exit /b 1
)

echo Starting 3DGS console on http://127.0.0.1:8000
"%PY%" app.py
if errorlevel 1 (
    echo.
    echo [ERROR] app.py exited with an error. See the window above for details.
)
pause
endlocal
