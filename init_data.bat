@echo off
rem Create an empty database (with schema) and the Bloomberg status file if they are missing.
rem Safe to run any time: never changes an existing database.
cd /d "%~dp0"
py -3 -c "from ui.app import ensure_schema, get_db_path; ensure_schema(get_db_path()); print(Database:, get_db_path())"
if errorlevel 1 pause
dir /b data\raw
pause
