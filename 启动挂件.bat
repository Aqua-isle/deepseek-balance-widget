@echo off
rem DeepSeek Balance Widget launcher
cd /d "%~dp0"
set "SCRIPT=%~dp0deepseek_balance_widget.py"

where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%SCRIPT%"
    exit /b
)

where python.exe >nul 2>nul
if %errorlevel%==0 (
    start "" python.exe "%SCRIPT%"
    exit /b
)

echo Python not found. Please install Python 3.9+ and enable "Add python.exe to PATH".
pause
