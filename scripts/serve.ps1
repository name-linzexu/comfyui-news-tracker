param(
    [switch]$SkipInstall,
    [switch]$Reload,
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$project = Split-Path -Parent $PSScriptRoot
Push-Location $project
try {
    $python = & "$PSScriptRoot\ensure-env.ps1" -SkipInstall:$SkipInstall
    $argsList = @("-m", "uvicorn", "app.main:app", "--host", $HostName, "--port", "$Port")
    if ($Reload) {
        $argsList += "--reload"
    }
    Write-Host "Serving http://$HostName`:$Port"
    & $python @argsList
}
finally {
    Pop-Location
}
