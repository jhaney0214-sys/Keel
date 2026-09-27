@echo off
rem Double-click: makes a starter folder beside the Keel folder, runs it,
rem and opens the report and the instructions.
setlocal
cd /d "%~dp0.."
set /p NAME=Name for the new folder (for example Riverbend FCU):
if "%NAME%"=="" exit /b 1
python -m keel init "..\%NAME%"
if errorlevel 1 (
  pause
  exit /b 1
)
python -m keel run "..\%NAME%"
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "..\%NAME%\report\report.html"
start "" notepad "..\%NAME%\START-HERE.txt"
