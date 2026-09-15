# tools/setup.ps1 -- the part of SETUP.cmd's logic that only makes sense once you already
# have a clone of this repository: install everything into .venv, create the database,
# install the `pnl` PowerShell function, run the tests, then doctor.
#
# SETUP.cmd is the one entry point for a brand-new PC (it can also install Python and Git,
# and clone the repository in the first place, which this script assumes already happened).
# This script exists so a developer who already has a clone can just run:
#   powershell -ExecutionPolicy Bypass -File tools\setup.ps1
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    py -3 risk.py setup
    if ($LASTEXITCODE -ne 0) { throw "risk.py setup failed with exit code $LASTEXITCODE" }
    py -3 risk.py doctor
    Write-Host ''
    Write-Host 'Ready. Open a PowerShell terminal and type: pnl'
} finally {
    Pop-Location
}
