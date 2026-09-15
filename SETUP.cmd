@echo off
rem SETUP.cmd -- double-click this once. Takes a Windows PC from "nothing installed" to
rem "ready to run": Python, Git, this repository, the app's own environment and tests,
rem the `pnl` PowerShell command, and a final health check. Safe to run again any time.
rem
rem This file is a polyglot: cmd.exe runs only the three lines above "exit /b", then the
rem embedded PowerShell script below the @POWERSHELL_SCRIPT@ marker runs everything else.
rem That means it works even when this single file is downloaded on its own, before the
rem repository has been cloned -- there is no tools\setup.ps1 to depend on yet at that
rem point, so the clone-or-pull step lives here too.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$src = [IO.File]::ReadAllText('%~f0'); $marker = '@POWERSHELL_SCRIPT@'; $i = $src.IndexOf($marker); iex ($src.Substring($i + $marker.Length))"
exit /b %errorlevel%
@POWERSHELL_SCRIPT@
$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/jeanwbourgeois02-boop/Henry_Risk_Calculator.git'
$DefaultClonePath = Join-Path $env:USERPROFILE 'risk-monitor'

function Say($m) { Write-Host $m }
function Ok($step, $detail = '') { Say ("OK      {0}{1}" -f $step, $(if ($detail) { " -- $detail" } else { '' })) }
function Failed($step, $reason) {
    Write-Host ("FAILED  {0} -- {1}" -f $step, $reason) -ForegroundColor Red
    Write-Host ''
    Write-Host 'Setup did not finish. Fix the problem above, then double-click SETUP.cmd again.'
    Read-Host 'Press Enter to close'
    exit 1
}

function Have-Command($name) {
    $null = Get-Command $name -ErrorAction SilentlyContinue
    return $?
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
        Failed 'python' 'py -3 is not installed and winget is not available. Install 64-bit Python 3.12+ from python.org (tick "py launcher" and "Add to PATH"), then re-run SETUP.cmd.'
    }
    Say '  py -3 not found; installing via winget (this can take a few minutes)...'
    winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { Failed 'python' 'winget install Python.Python.3.12 failed. Install Python 3.12+ from python.org manually, then re-run SETUP.cmd.' }
    Ok 'python' 'installed via winget'
    Write-Host '  NOTE: you may need to close and reopen this window (or re-run SETUP.cmd) for PATH to pick up "py".'
}

# (b) git
Say '[2/7] Git'
if (Have-Command 'git') {
    Ok 'git' (& git --version)
} else {
    if (-not (Have-Command 'winget')) {
        Failed 'git' 'git is not installed and winget is not available. Install Git from git-scm.com, then re-run SETUP.cmd.'
    }
    Say '  git not found; installing via winget...'
    winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { Failed 'git' 'winget install Git.Git failed. Install Git from git-scm.com manually, then re-run SETUP.cmd.' }
    Ok 'git' 'installed via winget'
}

# (c) clone or update the repository
Say '[3/7] Repository'
$here = $null
if ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $here) { $here = (Get-Location).Path }
if (Test-Path (Join-Path $here '.git')) {
    $RepoRoot = $here
    Push-Location $RepoRoot
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { Failed 'repository' "git pull --ff-only failed in $RepoRoot. Resolve manually (git status), then re-run SETUP.cmd." }
    Pop-Location
    Ok 'repository' "updated $RepoRoot"
} else {
    $RepoRoot = $DefaultClonePath
    if (Test-Path (Join-Path $RepoRoot '.git')) {
        Push-Location $RepoRoot
        git pull --ff-only
        if ($LASTEXITCODE -ne 0) { Failed 'repository' "git pull --ff-only failed in $RepoRoot. Resolve manually (git status), then re-run SETUP.cmd." }
        Pop-Location
        Ok 'repository' "updated existing clone at $RepoRoot"
    } else {
        Say "  cloning into $RepoRoot ..."
        git clone $RepoUrl $RepoRoot
        if ($LASTEXITCODE -ne 0) { Failed 'repository' "git clone $RepoUrl into $RepoRoot failed. Check your network / GitHub access, then re-run SETUP.cmd." }
        Ok 'repository' "cloned into $RepoRoot"
    }
}

# (d) py -3 risk.py setup -- creates .venv, installs requirements, creates the database,
#     installs the `pnl` PowerShell function (step (e) of the plan; risk.py setup does it),
#     and runs the tests.
Say '[4/7] py -3 risk.py setup (venv, packages, database, pnl command, tests)'
Push-Location $RepoRoot
py -3 risk.py setup
$setupCode = $LASTEXITCODE
Pop-Location
if ($setupCode -ne 0) { Failed 'risk.py setup' "exit code $setupCode -- see the output above for which step failed." }
Ok 'risk.py setup' 'venv, packages, database and tests are ready; pnl command installed'

# (f) doctor
Say '[5/7] py -3 risk.py doctor'
Push-Location $RepoRoot
py -3 risk.py doctor
$doctorCode = $LASTEXITCODE
Pop-Location
if ($doctorCode -ne 0) {
    Write-Host 'doctor found a problem (see above). Setup itself is otherwise complete; fix the doctor finding when convenient.' -ForegroundColor Yellow
} else {
    Ok 'doctor' 'everything checks out'
}

Say '[6/7] pnl command'
Ok 'pnl' 'installed into your PowerShell profile(s) by risk.py setup'

Say '[7/7] Done'
Write-Host '======================================================================'
Write-Host ' Ready. Open a PowerShell terminal and type: pnl'
Write-Host '======================================================================'
Read-Host 'Press Enter to close'
