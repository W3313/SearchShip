# Build dist\SearchShip.exe on Windows.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Needs Python 3.12 on PATH. Uses .venv if it exists, otherwise the current
# Python. Installs requirements-dev.txt (pywebview, pytest, PyInstaller) first
# unless -SkipInstall is given.
param([switch]$SkipInstall)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = 'python'
if (Test-Path '.venv\Scripts\python.exe') { $python = '.venv\Scripts\python.exe' }

if (-not $SkipInstall) {
    & $python -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
}

& $python -m PyInstaller --noconfirm --clean packaging\SearchShip.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$exe = Join-Path $root 'dist\SearchShip.exe'
if (-not (Test-Path $exe)) { throw "Build finished but $exe is missing" }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Built $exe ($mb MB)"
