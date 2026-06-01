$ErrorActionPreference = "Stop"

& "$PSScriptRoot\scripts\collect.ps1" -Mode refresh
& "$PSScriptRoot\scripts\serve.ps1" -Reload -SkipInstall
