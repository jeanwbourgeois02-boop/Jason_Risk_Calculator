@echo off
rem Checks that this folder is in sync with GitHub. Works on either computer:
rem  - on the dev PC it tells you whether your last commit has been pushed;
rem  - on the Bloomberg PC it tells you whether you have pulled the latest commit.
rem Read-only: it fetches but never pushes, pulls, or changes files.
cd /d "%~dp0"
echo ==========================================================
echo  risk-monitor: git sync check   (%CD%)
echo ==========================================================
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo FAILED: this folder is not a git repository.
  goto :end
)
for /f "delims=" %%r in ('git remote get-url origin 2^>nul') do set REMOTE=%%r
if "%REMOTE%"=="" (
  echo FAILED: no "origin" remote. Run:  git remote add origin https://github.com/^<you^>/^<repo^>.git
  goto :end
)
for /f "delims=" %%b in ('git branch --show-current') do set BRANCH=%%b
echo Remote : %REMOTE%
echo Branch : %BRANCH%
echo.
echo Fetching from GitHub ...
git fetch origin --quiet
if errorlevel 1 (
  echo FAILED: could not reach GitHub. Check internet access / login.
  goto :end
)
for /f "delims=" %%h in ('git rev-parse --short HEAD') do set LOCAL=%%h
for /f "delims=" %%h in ('git rev-parse --short origin/%BRANCH% 2^>nul') do set REMOTEHEAD=%%h
if "%REMOTEHEAD%"=="" (
  echo FAILED: branch "%BRANCH%" does not exist on GitHub yet. Run:  git push -u origin %BRANCH%
  goto :end
)
for /f %%n in ('git rev-list --count origin/%BRANCH%..HEAD') do set AHEAD=%%n
for /f %%n in ('git rev-list --count HEAD..origin/%BRANCH%') do set BEHIND=%%n
echo Local  : %LOCAL%
echo GitHub : %REMOTEHEAD%
echo.
git log --oneline -1
echo.
if "%AHEAD%"=="0" if "%BEHIND%"=="0" (
  echo OK: local and GitHub are identical. Nothing to push or pull.
  goto :uncommitted
)
if not "%AHEAD%"=="0" echo NOT PUSHED: %AHEAD% local commit(s) are not on GitHub. Run:  git push
if not "%BEHIND%"=="0" echo NOT PULLED: GitHub has %BEHIND% newer commit(s). Run:  git pull

:uncommitted
git status --porcelain > "%TEMP%\risk_git_status.txt"
for %%A in ("%TEMP%\risk_git_status.txt") do set SIZE=%%~zA
if not "%SIZE%"=="0" (
  echo.
  echo NOTE: uncommitted local changes exist ^(not on GitHub until committed and pushed^):
  git status --short
)

:end
echo.
pause
