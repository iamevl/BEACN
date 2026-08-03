#Requires -RunAsAdministrator
#Requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$RemoveConfig
)

$ErrorActionPreference = 'Stop'

$ServiceName = 'BeacnAgent'
$InstallDir = Join-Path $env:ProgramFiles 'BEACN'
$Wrapper = Join-Path $InstallDir 'BeacnAgentService.exe'
$ConfigPath = Join-Path $InstallDir 'config.json'
$PreservedConfig = Join-Path $env:ProgramData 'BEACN\config.json'

Write-Host ''
Write-Host 'BEACN Windows Agent Uninstaller' -ForegroundColor Cyan
Write-Host ''

$Service = Get-Service `
    -Name $ServiceName `
    -ErrorAction SilentlyContinue

if ($Service -and $Service.Status -ne 'Stopped') {
    Stop-Service `
        -Name $ServiceName `
        -Force

    $Service.WaitForStatus(
        'Stopped',
        [TimeSpan]::FromSeconds(30)
    )
}

if (Test-Path $Wrapper) {
    & $Wrapper uninstall | Out-Null
} elseif ($Service) {
    sc.exe delete $ServiceName | Out-Null
}

Get-NetFirewallRule `
    -DisplayName 'BEACN Agent API' `
    -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

Get-NetFirewallRule `
    -DisplayName 'BEACN iperf3' `
    -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

if (-not $RemoveConfig -and (Test-Path $ConfigPath)) {
    $PreservedConfigDir = Split-Path -Parent $PreservedConfig

    New-Item `
        -ItemType Directory `
        -Path $PreservedConfigDir `
        -Force |
        Out-Null

    Copy-Item `
        $ConfigPath `
        $PreservedConfig `
        -Force

    Write-Host "Configuration preserved at $PreservedConfig"
}

if (Test-Path $InstallDir) {
    Remove-Item `
        $InstallDir `
        -Recurse `
        -Force
}

Write-Host ''
Write-Host 'BEACN Agent removed.' -ForegroundColor Green

if ($RemoveConfig) {
    Write-Host 'Configuration removed.'
}