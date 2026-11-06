param([string]$Date = "auto")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location).Path ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) { $python = "python" }
& $python -m etf_temperature.cli run --date $Date --reuse-formal
exit $LASTEXITCODE
