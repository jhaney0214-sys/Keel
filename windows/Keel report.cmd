@echo off
rem Drag an institution folder onto this file: it runs the full report and opens it.
setlocal
if "%~1"=="" (
  echo Drag a Keel folder ^(one with assumptions.xlsx^) onto this file.
  pause
  exit /b 1
)
cd /d "%~dp0.."
python -m keel run "%~1"
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "%~1\report\report.html"
