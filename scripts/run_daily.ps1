$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m etf_temperature.cli run --date auto
exit $LASTEXITCODE
