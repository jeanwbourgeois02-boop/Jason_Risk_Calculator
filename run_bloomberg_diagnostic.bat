@echo off
rem Standalone Bloomberg diagnostic (read-only). Run on the Bloomberg computer.
cd /d "%~dp0"
py -3 tools\bloomberg_diagnostic.py --once
if errorlevel 1 (
  echo.
  echo Bloomberg unavailable or incomplete - see the report paths above.
) else (
  echo.
  echo All required Bloomberg checks passed.
)
pause
