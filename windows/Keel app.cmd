@echo off
rem Drag an institution folder onto this file: it opens the what-if, pricing,
rem new-product, trade and explore pages on this computer. Close this window to stop.
setlocal
if "%~1"=="" (
  echo Drag a Keel folder ^(one with assumptions.xlsx^) onto this file.
  pause
  exit /b 1
)
cd /d "%~dp0.."
start "" http://127.0.0.1:8750/
python -m keel serve "%~1"
pause
