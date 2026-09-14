@echo off
cd /d "%~dp0"
py -3 -m ui.launch
if errorlevel 1 pause
