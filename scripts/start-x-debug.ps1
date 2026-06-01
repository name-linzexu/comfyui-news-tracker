param(
  [int]$Port = 9222,
  [string]$ProfileDir = "",
  [string]$Url = "https://x.com/home",
  [string]$ChromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ChromePath)) {
  throw "Chrome not found at: $ChromePath"
}

if ([string]::IsNullOrWhiteSpace($ProfileDir)) {
  $ProfileDir = Join-Path $PSScriptRoot ".chrome-x-debug-profile"
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

$chromeArgs = @(
  "--remote-debugging-port=$Port",
  "--user-data-dir=$ProfileDir",
  "--no-first-run",
  "--no-default-browser-check",
  $Url
)

Start-Process -FilePath $ChromePath -ArgumentList $chromeArgs
Start-Sleep -Seconds 3

$versionUrl = "http://127.0.0.1:$Port/json/version"
try {
  $version = Invoke-RestMethod -Uri $versionUrl -TimeoutSec 5
  Write-Host "Chrome debug endpoint is ready:"
  Write-Host "  $versionUrl"
  Write-Host "  $($version.webSocketDebuggerUrl)"
  Write-Host ""
  Write-Host "If X asks for login, log in once in this Chrome profile:"
  Write-Host "  $ProfileDir"
} catch {
  Write-Host "Chrome started, but the debug endpoint is not ready yet:"
  Write-Host "  $versionUrl"
  Write-Host $_.Exception.Message
}
