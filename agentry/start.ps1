<#
.SYNOPSIS
    Start Agentry against this target repository.

.DESCRIPTION
    Runs in foreground until you Ctrl-C or close the terminal. There is no
    Windows Service install — every reboot, you run this script again.

    On first run, this script creates a local Python venv at
    <target>/agentry/.venv/ and pip-installs agentry into it. On subsequent
    runs it just activates the venv and starts the orchestrator.

    Run this script from the target repo root or from inside the agentry/
    folder — both work.

.EXAMPLE
    cd C:\projects\rpi-home-monitor
    .\agentry\start.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetRoot = Split-Path -Parent $ScriptDir
$Venv = Join-Path $ScriptDir '.venv'

# Locate Python.
$python = $null
foreach ($name in @('python', 'py')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Path; break }
}
if (-not $python) {
    Write-Host "Python not found on PATH." -ForegroundColor Red
    Write-Host "Run scripts/install-deps.ps1 from the agentry repo first:" -ForegroundColor Yellow
    Write-Host "  iwr -useb https://raw.githubusercontent.com/vinu-dev/agentry/main/scripts/install-deps.ps1 | iex" -ForegroundColor Yellow
    exit 1
}

# Create venv on first run; pip-install agentry into it.
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    Write-Host "==> First-time setup: creating venv at $Venv" -ForegroundColor Cyan
    & $python -m venv $Venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "venv creation failed" -ForegroundColor Red
        exit 1
    }
    & (Join-Path $Venv 'Scripts\python.exe') -m pip install --upgrade pip
    Write-Host "==> Installing agentry from GitHub" -ForegroundColor Cyan
    & (Join-Path $Venv 'Scripts\python.exe') -m pip install 'git+https://github.com/vinu-dev/agentry.git'
    if ($LASTEXITCODE -ne 0) {
        Write-Host "agentry install failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "==> Setup complete" -ForegroundColor Green
}

$AgentryExe = Join-Path $Venv 'Scripts\agentry.exe'
if (-not (Test-Path $AgentryExe)) {
    Write-Host "agentry binary not found at $AgentryExe — venv may be corrupted" -ForegroundColor Red
    Write-Host "Delete agentry\.venv and re-run this script." -ForegroundColor Yellow
    exit 1
}

Write-Host "==> Starting agentry against $TargetRoot" -ForegroundColor Cyan
& $AgentryExe start --target $TargetRoot
