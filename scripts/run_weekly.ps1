$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m etf_temperature.cli run-weekly --week auto
exit $LASTEXITCODE
