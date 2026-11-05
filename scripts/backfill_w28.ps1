$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot

try {
    Write-Host "[1/3] Backfilling official NAV data for 2026-07-06 through 2026-07-09..."
    & python -m etf_temperature.cli backfill-nav --start 20260706 --end 20260709
    if ($LASTEXITCODE -ne 0) { throw "Historical NAV backfill failed." }

    Write-Host "[2/3] Rebuilding daily snapshots for 2026-W28..."
    & python -m etf_temperature.cli rebuild --start 20260706 --end 20260710
    if ($LASTEXITCODE -ne 0) { throw "W28 daily snapshot rebuild failed." }

    Write-Host "[3/3] Generating the 2026-W28 weekly report..."
    & python -m etf_temperature.cli run-weekly --week 2026-W28
    if ($LASTEXITCODE -ne 0) { throw "W28 weekly report generation failed." }

    Write-Host "W28 backfill and weekly report workflow completed."
}
finally {
    Pop-Location
}
