@echo off
rem Import the sample BNP report in data\sample so the app has data to show.
rem Safe on an existing database: identical rows are skipped.
cd /d "%~dp0"
py -3 -c "from pathlib import Path; from ui.app import get_db_path, ensure_schema; from data.ingest.upload import import_report; p=Path(data/sample/HA_PNL_20260818.csv); ensure_schema(get_db_path()); print(import_report(p.read_bytes(), p.name, 2026-08-17, get_db_path()))"
pause
