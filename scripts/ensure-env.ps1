param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$project = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $project ".venv"
$python = Join-Path $venvDir "Scripts\python.exe"
$requirements = Join-Path $project "requirements.txt"
$stamp = Join-Path $venvDir ".requirements.sha256"

if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..."
    python -m venv $venvDir | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
}

$python = (Resolve-Path $python).Path

if (-not $SkipInstall) {
    $currentHash = (Get-FileHash $requirements -Algorithm SHA256).Hash
    $installedHash = ""
    if (Test-Path $stamp) {
        $installedHash = (Get-Content $stamp -Raw).Trim()
    }
    if ($currentHash -ne $installedHash) {
        Write-Host "Installing Python dependencies..."
        & $python -m pip install -r $requirements 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install Python dependencies."
        }
        Set-Content -Path $stamp -Value $currentHash -Encoding ASCII
    }
}

Write-Output $python
