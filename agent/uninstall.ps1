#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $BaseDir

$service = Get-Service NetworkDashboardAgent -ErrorAction SilentlyContinue

if ($service) {
    if ($service.Status -ne 'Stopped') {
        Stop-Service NetworkDashboardAgent -Force
    }

    if (Test-Path '.\.venv\Scripts\python.exe') {
        & '.\.venv\Scripts\python.exe' '.\agent_service.py' remove
    } else {
        sc.exe delete NetworkDashboardAgent | Out-Null
    }
}

Get-NetFirewallRule -DisplayName 'Network Dashboard Agent API' `
    -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Get-NetFirewallRule -DisplayName 'Network Dashboard iperf3' `
    -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Write-Host 'Network Dashboard Agent removed.' -ForegroundColor Green
