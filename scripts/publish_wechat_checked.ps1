param(
  [Parameter(Mandatory = $true)]
  [string]$Report,

  [Parameter(Mandatory = $true)]
  [string]$Title,

  [Parameter(Mandatory = $true)]
  [string]$Summary,

  [Parameter(Mandatory = $true)]
  [string]$Cover,

  [string]$Theme = "default",

  [int]$MaxHtmlBytes = 19000,

  [string]$WechatSkillDir = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($WechatSkillDir)) {
  if (-not [string]::IsNullOrWhiteSpace($env:WECHAT_SKILL_DIR)) {
    $WechatSkillDir = $env:WECHAT_SKILL_DIR
  }
  else {
    $WechatSkillDir = Join-Path $env:USERPROFILE ".codex\skills\baoyu-post-to-wechat"
  }
}

function Resolve-ExistingPath([string]$PathText, [string]$Label) {
  $resolved = Resolve-Path -LiteralPath $PathText -ErrorAction SilentlyContinue
  if (-not $resolved) {
    throw "$Label not found: $PathText"
  }
  return $resolved.Path
}

function Assert-FormalReport([string]$ResolvedReportPath) {
  $reportDir = Split-Path -Parent $ResolvedReportPath
  $dailyValidation = Join-Path $reportDir "validation.md"
  $weeklyValidation = Join-Path $reportDir "weekly_validation.json"
  if (Test-Path -LiteralPath $dailyValidation) {
    $validationText = Get-Content -LiteralPath $dailyValidation -Raw -Encoding UTF8
    if ($validationText -notmatch '(?m)^- formal_report_allowed: True\s*$') {
      throw "Daily report is not formal; WeChat publishing blocked: $dailyValidation"
    }
    return
  }
  if (Test-Path -LiteralPath $weeklyValidation) {
    $validation = Get-Content -LiteralPath $weeklyValidation -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $validation.formal_weekly_allowed) {
      throw "Weekly report is not formal; WeChat publishing blocked: $weeklyValidation"
    }
    return
  }
  throw "Formal validation artifact not found next to report: $ResolvedReportPath"
}

$reportPath = Resolve-ExistingPath $Report "Report"
$coverPath = Resolve-ExistingPath $Cover "Cover"
Assert-FormalReport $reportPath
$mdToWechat = Join-Path $WechatSkillDir "scripts\md-to-wechat.ts"
$wechatApi = Join-Path $WechatSkillDir "scripts\wechat-api.ts"

if (-not (Test-Path -LiteralPath $mdToWechat)) {
  throw "md-to-wechat.ts not found: $mdToWechat"
}
if (-not (Test-Path -LiteralPath $wechatApi)) {
  throw "wechat-api.ts not found: $wechatApi"
}

$stderrPath = [System.IO.Path]::GetTempFileName()
try {
  $preflightArgs = @("-y", "bun", $mdToWechat, $reportPath, "--theme", $Theme, "--title", $Title)
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $jsonText = & npx @preflightArgs 2>$stderrPath
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($LASTEXITCODE -ne 0) {
    $err = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
    throw "WeChat HTML preflight conversion failed: $err"
  }

  $preflight = $jsonText | ConvertFrom-Json
  $htmlPath = $preflight.htmlPath
  $htmlBytes = (Get-Item -LiteralPath $htmlPath).Length
  $markdownBytes = (Get-Item -LiteralPath $reportPath).Length

  Write-Host "[wechat-check] Markdown bytes: $markdownBytes"
  Write-Host "[wechat-check] Rendered HTML bytes: $htmlBytes"
  Write-Host "[wechat-check] Limit: $MaxHtmlBytes"

  if ($htmlBytes -gt $MaxHtmlBytes) {
    Write-Warning "Rendered WeChat HTML exceeds size limit. API upload stopped. Please upload manually; report content is unchanged."
    Write-Host "[wechat-check] Report: $reportPath"
    Write-Host "[wechat-check] Cover: $coverPath"
    Write-Host "[wechat-check] HTML preview: $htmlPath"
    Write-Host "[wechat-check] Title: $Title"
    Write-Host "[wechat-check] Summary: $Summary"
    exit 2
  }

  Write-Host "[wechat-check] Size OK. Publishing draft via API..."
  $publishArgs = @(
    "-y",
    "bun",
    $wechatApi,
    $reportPath,
    "--theme",
    $Theme,
    "--title",
    $Title,
    "--summary",
    $Summary,
    "--cover",
    $coverPath
  )
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & npx @publishArgs
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  exit $LASTEXITCODE
}
finally {
  Remove-Item -LiteralPath $stderrPath -ErrorAction SilentlyContinue
}
