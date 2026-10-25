<#
  One-command runner for the UI and API test framework.

  .\tests\run_tests.ps1                       # full UI + API suite (read-only)
  .\tests\run_tests.ps1 -Suite smoke          # critical path
  .\tests\run_tests.ps1 -Suite scroll         # any marker: journey, component, crawler, behavior,
                                              #   scroll, keyboard, responsive, visual, resilience, quality
  .\tests\run_tests.ps1 -Suite api            # API tests only
  .\tests\run_tests.ps1 -Writes               # also run data-changing tests (DEV database only)
  .\tests\run_tests.ps1 -Browsers chromium,firefox,webkit -Workers 4 -Headed
  .\tests\run_tests.ps1 -UpdateSnapshots      # refresh visual baselines
#>
param(
  [string]$Suite = "all",
  [string[]]$Browsers = @("chromium"),
  [int]$Workers = 1,
  [switch]$Writes,
  [switch]$Headed,
  [switch]$UpdateSnapshots,
  [switch]$Install,
  [string]$WebUrl = $(if ($env:AIP_WEB_URL) { $env:AIP_WEB_URL } else { "http://localhost:3000" }),
  [string]$ApiUrl = $(if ($env:AIP_API_URL) { $env:AIP_API_URL } else { "http://127.0.0.1:8001" })
)

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  if ($Install) {
    python -m pip install -r tests/ui/requirements.txt
    python -m playwright install --with-deps @Browsers
  }

  $env:AIP_WEB_URL = $WebUrl
  $env:AIP_API_URL = $ApiUrl
  $env:AIP_UI_ALLOW_WRITES = $(if ($Writes) { "1" } else { "0" })
  $env:AIP_API_ALLOW_WRITES = $env:AIP_UI_ALLOW_WRITES
  $env:AIP_UI_UPDATE_SNAPSHOTS = $(if ($UpdateSnapshots) { "1" } else { "0" })

  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $out = "tests/ui/artifacts/$stamp"
  New-Item -ItemType Directory -Force -Path $out | Out-Null

  $args = @("-p", "no:cacheprovider", "-ra", "--junitxml=$out/junit.xml",
            "--html=$out/report.html", "--self-contained-html",
            "--tracing=retain-on-failure", "--video=retain-on-failure", "--screenshot=only-on-failure",
            "--output=$out/playwright", "--reruns", "1")
  foreach ($b in $Browsers) { $args += @("--browser", $b) }
  if ($Workers -gt 1) { $args += @("-n", "$Workers") }
  if ($Headed) { $args += "--headed" }

  switch ($Suite) {
    "all"   { $targets = @("tests/ui", "tests/api") }
    "ui"    { $targets = @("tests/ui") }
    "api"   { $targets = @("tests/api") }
    default { $targets = @("tests/ui"); $args += @("-m", $Suite) }
  }

  python -m pytest @targets @args
  $code = $LASTEXITCODE
  Write-Host "`nReport: $out/report.html   JUnit: $out/junit.xml   Traces: $out/playwright"
  exit $code
}
finally { Pop-Location }
