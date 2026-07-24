#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $BaseDir

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' not found. Install Python 3.12 first."
}

if (-not (Test-Path '.\iperf3.exe')) {
    throw "iperf3.exe and its DLL files are missing from $BaseDir."
}

if (-not (Test-Path '.\hardware-helper.exe')) {
    throw "hardware-helper.exe is missing. Run build-helper.ps1 or use a packaged GitHub release."
}

if (-not (Test-Path '.\config.json')) {
    Copy-Item '.\config.example.json' '.\config.json'
}

if (-not (Test-Path '.\.venv')) {
    py -3.12 -m venv .venv
}

& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python requirements."
}

$existing = Get-Service NetworkDashboardAgent -ErrorAction SilentlyContinue

if ($existing) {
    if ($existing.Status -ne 'Stopped') {
        Stop-Service NetworkDashboardAgent -Force
    }

    & '.\.venv\Scripts\python.exe' '.\agent_service.py' remove
    Start-Sleep -Seconds 2
}

& '.\.venv\Scripts\python.exe' '.\agent_service.py' --startup auto install
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the Windows service."
}

Get-NetFirewallRule -DisplayName 'Network Dashboard Agent API' `
    -ErrorAction SilentlyContinue | Remove-NetFirewallRule

New-NetFirewallRule `
    -DisplayName 'Network Dashboard Agent API' `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 8767 `
    -RemoteAddress LocalSubnet `
    -Profile Any | Out-Null

Get-NetFirewallRule -DisplayName 'Network Dashboard iperf3' `
    -ErrorAction SilentlyContinue | Remove-NetFirewallRule

New-NetFirewallRule `
    -DisplayName 'Network Dashboard iperf3' `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 5201 `
    -RemoteAddress LocalSubnet `
    -Profile Any | Out-Null

Start-Service NetworkDashboardAgent
Start-Sleep -Seconds 3

$status = Invoke-RestMethod 'http://localhost:8767/status'
if (-not $status.agent -or $status.agent.version -ne '0.5.0') {
    throw "Service started, but the v0.5 status check failed."
}

Write-Host 'Network Dashboard Agent v0.5 installed and started.' -ForegroundColor Green
Write-Host "Hardware provider available: $($status.hardware.available)"
Write-Host 'API: http://localhost:8767/status'
Write-Host 'Hardware: http://localhost:8767/hardware'
