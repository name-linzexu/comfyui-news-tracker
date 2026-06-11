param(
    [ValidateSet("refresh", "rescore", "export", "all")]
    [string]$Mode = "refresh",

    [switch]$Fast,
    [switch]$SkipX,
    [switch]$SkipGithubSearch,
    [switch]$NoWebhook,
    [switch]$Json,
    [switch]$Quiet,
    [switch]$SkipInstall,
    [switch]$LlmTriage,
    [int]$LlmTriageLimit = 40,
    [int]$LlmTriageMinScore = 45,
    [switch]$LlmTriageIncludeReviewed,
    [switch]$OpenBrowser,
    [switch]$StartServer,
    [string]$Day,
    [string]$OutDir,
    [string[]]$IncludeType = @(),
    [string[]]$ExcludeType = @(),
    [string[]]$SourceId = @(),
    [string[]]$SkipSourceId = @()
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$project = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $project "data\logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$logFile = Join-Path $logsDir ("refresh-{0}-{1}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss-fff"), $PID)

function Add-Arg {
    param(
        [System.Collections.Generic.List[string]]$List,
        [string]$Name,
        [string]$Value
    )
    if ($Value) {
        $List.Add($Name)
        $List.Add($Value)
    }
}

function Add-MultiArg {
    param(
        [System.Collections.Generic.List[string]]$List,
        [string]$Name,
        [string[]]$Values
    )
    foreach ($value in $Values) {
        if ($value) {
            $List.Add($Name)
            $List.Add($value)
        }
    }
}

Push-Location $project
try {
    $python = & "$PSScriptRoot\ensure-env.ps1" -SkipInstall:$SkipInstall
    $argsList = [System.Collections.Generic.List[string]]::new()
    $argsList.Add("scripts\refresh.py")
    Add-Arg $argsList "--mode" $Mode
    Add-MultiArg $argsList "--include-type" $IncludeType
    Add-MultiArg $argsList "--exclude-type" $ExcludeType
    Add-MultiArg $argsList "--source-id" $SourceId
    Add-MultiArg $argsList "--skip-source-id" $SkipSourceId
    Add-Arg $argsList "--day" $Day
    Add-Arg $argsList "--out-dir" $OutDir

    if ($Fast) { $argsList.Add("--fast") }
    if ($SkipX) { $argsList.Add("--skip-x") }
    if ($SkipGithubSearch) { $argsList.Add("--skip-github-search") }
    if ($NoWebhook) { $argsList.Add("--no-webhook") }
    if ($Json) { $argsList.Add("--json") }
    if ($Quiet -or -not $Json) { $argsList.Add("--quiet") }
    if ($LlmTriage) {
        $argsList.Add("--llm-triage")
        $argsList.Add("--llm-triage-limit")
        $argsList.Add([string]$LlmTriageLimit)
        $argsList.Add("--llm-triage-min-score")
        $argsList.Add([string]$LlmTriageMinScore)
    }
    if ($LlmTriageIncludeReviewed) { $argsList.Add("--llm-triage-include-reviewed") }

    $started = Get-Date
    "[$($started.ToString("s"))] $python $($argsList -join ' ')" | Tee-Object -FilePath $logFile | Out-Host
    & $python @argsList 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Collector failed. See log: $logFile"
    }
    $finished = Get-Date
    "[$($finished.ToString("s"))] done in $([int]($finished - $started).TotalSeconds)s; log=$logFile" |
        Tee-Object -FilePath $logFile -Append | Out-Host

    if ($OpenBrowser) {
        Start-Process "http://127.0.0.1:8787"
    }
    if ($StartServer) {
        & "$PSScriptRoot\serve.ps1" -SkipInstall:$true
    }
}
finally {
    Pop-Location
}
