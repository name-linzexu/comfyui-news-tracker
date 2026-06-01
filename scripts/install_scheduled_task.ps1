$ErrorActionPreference = "Stop"

$project = Split-Path -Parent $PSScriptRoot
$script = Join-Path $project "scripts\collect.ps1"
$taskName = "ComfyUI News Tracker Refresh"

if (-not (Test-Path (Join-Path $project ".venv\Scripts\python.exe"))) {
    throw "Virtual environment not found. Run .\run.ps1 once before installing the scheduled task."
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Mode refresh -Quiet" `
    -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 2)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Installed scheduled task: $taskName"
