@echo off
rem Rebuild the P&L ledger history from Bloomberg daily closes. Run on the Bloomberg computer.
rem Default range: last business day of the previous year .. yesterday. Days already complete are skipped.
rem Optional: backfill_history.bat 2026-06-01 2026-09-12
cd /d "%~dp0"
if "%~1"=="" (
  py -3 -m data.bloomberg.backfill
) else (
  py -3 -m data.bloomberg.backfill --start %1 --end %2
)
pause
