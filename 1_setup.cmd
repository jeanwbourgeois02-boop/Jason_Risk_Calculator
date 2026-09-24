@echo off
rem 1_setup.cmd -- double-click this once. Takes a Windows PC from "nothing installed" to
rem "ready to run": Python, Git, this repository, the app's own environment and tests,
rem the `chelsea` PowerShell command, and a final health check. Safe to run again any time.
rem
rem This file is a polyglot: cmd.exe runs only the lines above "exit /b", then the
rem embedded PowerShell script below the marker line (the line right after "exit /b";
rem it must sit on a line of its own, with nothing else on it, because the wrapper
rem looks for it as a whole line, never as a substring) runs everything else.
rem That means it works even when this single file is downloaded on its own, before the
rem repository has been cloned -- there is no tools\setup.ps1 to depend on yet at that
rem point, so the clone-or-pull step lives here too. If the PowerShell part ends with
rem a non-zero exit code, the window stays open until a key is pressed, so the error
rem can be read.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$lines = [IO.File]::ReadAllLines('%~f0'); $i = [Array]::IndexOf($lines, '@POWERSHELL_SCRIPT@'); if ($i -lt 0) { throw '1_setup.cmd: marker line @POWERSHELL_SCRIPT@ not found' }; $SetupCmdPath = '%~f0'; iex (($lines[($i + 1)..($lines.Length - 1)]) -join [char]10)"
set "rc=%errorlevel%"
if not "%rc%"=="0" echo Setup did not finish (exit code %rc%). Read the message above, then press a key to close.
if not "%rc%"=="0" pause
exit /b %rc%
@POWERSHELL_SCRIPT@
$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/jeanwbourgeois02-boop/Jason_Risk_Calculator.git'
$DefaultClonePath = Join-Path $env:USERPROFILE 'Jason Risk Monitor'

function Say($m) { Write-Host $m }
function Ok($step, $detail = '') { Say ("OK      {0}{1}" -f $step, $(if ($detail) { " -- $detail" } else { '' })) }
function Failed($step, $reason) {
    Write-Host ("FAILED  {0} -- {1}" -f $step, $reason) -ForegroundColor Red
    Write-Host ''
    Write-Host 'Setup did not finish. Fix the problem above, then double-click 1_setup.cmd again.'
    Read-Host 'Press Enter to close'
    exit 1
}

function Have-Command($name) {
    $null = Get-Command $name -ErrorAction SilentlyContinue
    return $?
}

function Refresh-Path {
    # winget writes the new PATH to the registry; this already-running process keeps its
    # start-up snapshot, so re-read Machine + User PATH before calling `py` or `git`.
    $env:PATH = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

Write-Host '======================================================================'
Write-Host ' risk-monitor SETUP'
Write-Host '======================================================================'

# (a) Python 3.12+
Say '[1/7] Python 3.12+'
$havePy = $false
try { $v = & py -3 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null; if ($LASTEXITCODE -eq 0 -and $v) { $havePy = $true } } catch {}
if ($havePy) {
    Ok 'python' "py -3 -> $v"
} else {
    if (-not (Have-Command 'winget')) {
        Failed 'python' 'py -3 is not installed and winget is not available. Install 64-bit Python 3.12+ from python.org (tick "py launcher" and "Add to PATH"), then re-run 1_setup.cmd.'
    }
    Say '  py -3 not found; installing via winget (this can take a few minutes)...'
    winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { Failed 'python' 'winget install Python.Python.3.12 failed. Install Python 3.12+ from python.org manually, then re-run 1_setup.cmd.' }
    Refresh-Path
    if (-not (Have-Command 'py')) {
        Failed 'python' 'Python installed, but this window cannot see the "py" launcher yet. Close this window and double-click 1_setup.cmd again.'
    }
    Ok 'python' 'installed via winget'
}

# (b) git
Say '[2/7] Git'
if (Have-Command 'git') {
    Ok 'git' (& git --version)
} else {
    if (-not (Have-Command 'winget')) {
        Failed 'git' 'git is not installed and winget is not available. Install Git from git-scm.com, then re-run 1_setup.cmd.'
    }
    Say '  git not found; installing via winget...'
    winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { Failed 'git' 'winget install Git.Git failed. Install Git from git-scm.com manually, then re-run 1_setup.cmd.' }
    Refresh-Path
    if (-not (Have-Command 'git')) {
        Failed 'git' 'Git installed, but this window cannot see it yet. Close this window and double-click 1_setup.cmd again.'
    }
    Ok 'git' 'installed via winget'
}

# (c) clone or update the repository
Say '[3/7] Repository'
# $SetupCmdPath is set by the cmd wrapper above (the full path of this file): under
# iex, $MyInvocation.MyCommand.Path is null, and the current directory is System32
# on "Run as administrator", so neither would find the folder this file sits in.
$here = $null
if ($SetupCmdPath) { $here = Split-Path -Parent $SetupCmdPath }
if (-not $here -and $MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $here) { $here = (Get-Location).Path }

function Normalize-RepoUrl($url) {
    # One comparable form for a git remote URL: no scheme, no credentials, no trailing
    # slash, no trailing .git, lower case, and the scp-like ssh form (git@host:path)
    # rewritten as host/path, so the https and ssh forms of one repository compare equal.
    if (-not $url) { return '' }
    $u = "$url".Trim()
    if (-not $u) { return '' }
    $u = $u -replace '^(ssh|git\+ssh|git|https?)://', ''
    $u = $u -replace '^[^/@]*@', ''
    $u = $u -replace '^([^/:]+):', '$1/'
    $u = $u.TrimEnd('/')
    if ($u -match '\.git$') { $u = $u.Substring(0, $u.Length - 4) }
    return $u.TrimEnd('/').ToLowerInvariant()
}

function Origin-Url($path) {
    # The folder's origin remote, or '' when it is not a git clone or has no origin.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & git -C "$path" remote get-url origin 2>$null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0 -or -not $out) { return '' }
    return ("$out" -split "`n")[0].Trim()
}

function Assert-OwnRepo($path) {
    # Two diverging projects can share a PC, so this installer only ever updates its own
    # project's clone: a folder whose origin is another repository is left exactly as it
    # is. A folder that is not a clone, or that has no origin, is not a mismatch.
    $origin = Origin-Url $path
    if (-not $origin) { return }
    if ((Normalize-RepoUrl $origin) -eq (Normalize-RepoUrl $RepoUrl)) { return }
    Failed 'repository' ("the folder $path belongs to a different project: its origin is $origin, but this 1_setup.cmd installs $RepoUrl. Run that project's own 1_setup.cmd instead, or install this project into a folder of its own.")
}

function Update-Clone($path) {
    # Same rule as 2_launcher.py's sync_with_github: local edits are set aside with
    # git stash (git stash pop restores them), then fast-forward to origin/main.
    Push-Location -LiteralPath $path
    if (git status --porcelain) {
        git stash push --include-untracked -m "setup auto-stash $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
        Write-Host '  local edits were set aside with git stash (git stash pop restores them)' -ForegroundColor Yellow
    }
    git fetch origin main --quiet
    if ($LASTEXITCODE -ne 0) { Pop-Location; Failed 'repository' "git fetch failed in $path. Check your network / GitHub access, then re-run 1_setup.cmd." }
    git merge --ff-only origin/main
    if ($LASTEXITCODE -ne 0) { Pop-Location; Failed 'repository' "could not fast-forward $path to origin/main (local commits?). Resolve manually (git status), then re-run 1_setup.cmd." }
    Pop-Location
}

if (Test-Path -LiteralPath (Join-Path $here '.git')) {
    $RepoRoot = $here
    Assert-OwnRepo $RepoRoot
    Update-Clone $RepoRoot
    Ok 'repository' "updated $RepoRoot"
} else {
    $RepoRoot = $DefaultClonePath
    if (Test-Path -LiteralPath (Join-Path $RepoRoot '.git')) {
        Assert-OwnRepo $RepoRoot
        Update-Clone $RepoRoot
        Ok 'repository' "updated existing clone at $RepoRoot"
    } else {
        Say "  cloning into $RepoRoot ..."
        git clone $RepoUrl "$RepoRoot"
        if ($LASTEXITCODE -ne 0) { Failed 'repository' "git clone $RepoUrl into $RepoRoot failed. Check your network / GitHub access, then re-run 1_setup.cmd." }
        Ok 'repository' "cloned into $RepoRoot"
    }
}

Say ("  using {0} in {1}" -f $RepoUrl, $RepoRoot)

# (d) py -3 2_launcher.py setup -- creates .venv, installs requirements, creates the database,
#     installs the `chelsea` PowerShell function (step (e) of the plan; 2_launcher.py setup does it),
#     and runs the tests.
Say '[4/7] py -3 2_launcher.py setup (venv, packages, database, chelsea command, tests)'
Push-Location -LiteralPath $RepoRoot
py -3 2_launcher.py setup
$setupCode = $LASTEXITCODE
Pop-Location
if ($setupCode -ne 0) { Failed '2_launcher.py setup' "exit code $setupCode -- see the output above for which step failed." }
Ok '2_launcher.py setup' 'venv, packages, database and tests are ready; chelsea command installed'

# (f) doctor
Say '[5/7] py -3 2_launcher.py doctor'
Push-Location -LiteralPath $RepoRoot
py -3 2_launcher.py doctor
$doctorCode = $LASTEXITCODE
Pop-Location
if ($doctorCode -ne 0) {
    Write-Host 'doctor found a problem (see above). Setup itself is otherwise complete; fix the doctor finding when convenient.' -ForegroundColor Yellow
} else {
    Ok 'doctor' 'everything checks out'
}

Say '[6/7] chelsea command'
Ok 'chelsea' 'installed into your PowerShell profile(s) by 2_launcher.py setup'

Say '[7/7] Done'
Write-Host '======================================================================'
Write-Host ' Ready. Open a PowerShell terminal and type: chelsea'
Write-Host '======================================================================'
Read-Host 'Press Enter to close'
