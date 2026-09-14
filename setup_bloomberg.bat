@echo off
rem One-time setup on the Bloomberg computer. Installs Python packages only.
rem Makes no code, UI, schema, valuation or database changes.
rem Uses the same interpreter as launch.bat and run_bloomberg_diagnostic.bat: "py -3".
cd /d "%~dp0"
echo ==========================================================
echo  risk-monitor: Bloomberg computer setup
echo  Folder: %CD%
echo ==========================================================
echo.

py -3 -c "import sys; print('Python', sys.version.split()[0], 'at', sys.executable)"
if errorlevel 1 (
  echo FAILED: "py -3" did not start. Install Python 3 64-bit from python.org
  echo         and tick "Add python.exe to PATH" / "py launcher", then re-run.
  goto :end
)
echo.

echo [1/3] Installing application requirements ...
py -3 -m pip install --upgrade pip >nul 2>&1
py -3 -m pip install -r requirements.txt
if errorlevel 1 (
  echo FAILED: requirements.txt did not install. Check the messages above and your internet access.
  goto :end
)
echo OK: application requirements installed.
echo.

echo [2/3] Installing blpapi from Bloomberg's official package index ...
py -3 -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
if errorlevel 1 (
  echo FAILED: blpapi did not install. Check that this PC can reach blpapi.bloomberg.com
  echo         and that Python is 64-bit (blpapi wheels are 64-bit).
  goto :end
)
echo OK: blpapi installed.
echo.

echo [3/3] Verifying imports ...
py -3 -c "import dash, pandas, openpyxl, xlrd, blpapi; print('dash', dash.__version__, '| pandas', pandas.__version__, '| blpapi', blpapi.__version__)"
if errorlevel 1 (
  echo FAILED: a package installed but does not import. See the error above.
  goto :end
)
echo.
if not exist "data\raw\risk.db" (
  echo NOTE: data\raw\risk.db is not present yet. Copy it from the other computer into
  echo       %CD%\data\raw\  before launching, or upload the BNP CSV in the app.
) else (
  echo OK: data\raw\risk.db found.
)
echo.
echo ==========================================================
echo  SETUP COMPLETE.
echo  Next: 1. log in to the Bloomberg Terminal
echo        2. double-click launch.bat
echo        3. double-click run_bloomberg_diagnostic.bat
echo ==========================================================

:end
echo.
pause
