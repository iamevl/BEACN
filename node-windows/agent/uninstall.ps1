#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $BaseDir

$service = Get-Service BeacnAgent -ErrorAction SilentlyContinue

if ($service) {
    if ($service.Status -ne 'Stopped') {
        Stop-Service BeacnAgent -Force
    }

    if (Test-Path '.\.venv\Scripts\python.exe') {
        & '.\.venv\Scripts\python.exe' '.\agent_service.py' remove
    } else {
        sc.exe delete BeacnAgent | Out-Null
    }
}

Get-NetFirewallRule -DisplayName 'BEACN Agent API' `
    -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Get-NetFirewallRule -DisplayName 'BEACN iperf3' `
    -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Write-Host 'BEACN Agent removed.' -ForegroundColor Green
