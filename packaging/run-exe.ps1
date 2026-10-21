# Run dist\SearchShip.exe for a CI check and report what it did.
#
# The exe is windowed (no console), so it cannot print to this terminal:
# results go to the --out file and anything printed goes to searchship.log in
# the data folder. This waits with a timeout (a stuck window or an error
# dialog must not hang the job), prints both files, and exits with the exe's
# exit code (124 on timeout).
#
#   ./packaging/run-exe.ps1 -Arguments 'selftest' -OutFile "$env:RUNNER_TEMP\selftest.txt"
#   ./packaging/run-exe.ps1 -Arguments 'selftest','--gui' -OutFile "$env:RUNNER_TEMP\gui.txt" -TimeoutSec 300
param(
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [string]$OutFile = '',
    [int]$TimeoutSec = 240
)

$ErrorActionPreference = 'Stop'
$exe = Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\SearchShip.exe'
if (-not (Test-Path $exe)) { throw "$exe not found; build it first" }

$argList = @($Arguments)
if ($OutFile) {
    Remove-Item -LiteralPath $OutFile -ErrorAction SilentlyContinue
    $argList += @('--out', "`"$OutFile`"")
}
# Only touch the log in a throwaway data folder, never in %APPDATA%.
$log = $null
if ($env:SEARCHSHIP_HOME) {
    $log = Join-Path $env:SEARCHSHIP_HOME 'searchship.log'
    Remove-Item -LiteralPath $log -ErrorAction SilentlyContinue
}

Write-Host "> SearchShip.exe $($argList -join ' ')"
$p = Start-Process -FilePath $exe -ArgumentList $argList -PassThru
$null = $p.Handle   # hold a handle so ExitCode can still be read after the process exits
if ($p.WaitForExit($TimeoutSec * 1000)) {
    $code = $p.ExitCode
    if ($null -eq $code) { Write-Host 'Could not read the exit code.'; $code = 1 }
} else {
    Write-Host "Timed out after $TimeoutSec s; stopping it."
    try { $p.Kill($true) }  # whole process tree (PowerShell 7)
    catch { & taskkill.exe /T /F /PID $p.Id | Out-Host }
    $code = 124
}
Write-Host "exit code: $code"

if ($OutFile) {
    if (Test-Path -LiteralPath $OutFile) { Write-Host "--- $OutFile"; Get-Content -LiteralPath $OutFile }
    else { Write-Host '(no --out file was written)' }
}
if ($log -and (Test-Path -LiteralPath $log)) { Write-Host '--- searchship.log'; Get-Content -LiteralPath $log }
exit $code
