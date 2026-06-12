param(
    [ValidateSet("refresh", "rescore", "export", "all")]
    [string]$Mode = "all",

    [ValidateSet("Daily", "Hourly")]
    [string]$Schedule = "Daily",

    [string]$At = "09:00",
    [int]$EveryHours = 2,
    [switch]$NoWebhook,
    [switch]$LlmTriage,
    [int]$LlmTriageLimit = 40,
    [int]$LlmTriageMinScore = 45
)

$ErrorActionPreference = "Stop"

$project = Split-Path -Parent $PSScriptRoot
$script = Join-Path $project "scripts\collect.ps1"
$taskName = "ComfyUI News Tracker Refresh"

if (-not (Test-Path (Join-Path $project ".venv\Scripts\python.exe"))) {
    throw "Virtual environment not found. Run .\run.ps1 once before installing the scheduled task."
}

$collectArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Mode $Mode -Quiet"
if ($NoWebhook) {
    $collectArgs += " -NoWebhook"
}
if ($LlmTriage) {
    $collectArgs += " -LlmTriage -LlmTriageLimit $LlmTriageLimit -LlmTriageMinScore $LlmTriageMinScore"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $collectArgs `
    -WorkingDirectory $project

if ($Schedule -eq "Hourly") {
    if ($EveryHours -lt 1) {
        throw "EveryHours must be at least 1."
    }
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Hours $EveryHours) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $scheduleText = "every $EveryHours hour(s), starting in 5 minutes"
}
else {
    $parsedTime = [datetime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
    $atTime = (Get-Date).Date.Add($parsedTime.TimeOfDay)
    $trigger = New-ScheduledTaskTrigger -Daily -At $atTime
    $scheduleText = "daily at $At local time"
}

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
$actionText = "collect.ps1 -Mode $Mode -Quiet"
if ($NoWebhook) { $actionText += " -NoWebhook" }
if ($LlmTriage) { $actionText += " -LlmTriage -LlmTriageLimit $LlmTriageLimit -LlmTriageMinScore $LlmTriageMinScore" }
Write-Host "Installed scheduled task: $taskName"
Write-Host "Schedule: $scheduleText"
Write-Host "Action: $actionText"
if (-not $env:COMFYUI_NEWS_WEBHOOK_URL -and -not (Test-Path (Join-Path $project ".secrets\webhook_url.txt"))) {
    Write-Host "Webhook is not configured. Add COMFYUI_NEWS_WEBHOOK_URL or .secrets\webhook_url.txt if you want external push delivery."
}
