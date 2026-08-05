#Requires -RunAsAdministrator
#Requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Install', 'Upgrade', 'Repair')]
    [string]$Mode = 'Auto'
)

$ErrorActionPreference = 'Stop'

$Version = '0.9.2'
$ServiceName = 'BeacnAgent'
$InstallDir = Join-Path $env:ProgramFiles 'BEACN'
$ConfigPath = Join-Path $InstallDir 'config.json'
$HealthUrl = 'http://127.0.0.1:8767/health'
$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$RequiredFiles = @(
    'BeacnAgent.exe',
    '_internal',
    'BeacnAgentService.exe',
    'BeacnAgentService.xml',
    'config.json'
)

function Write-Banner {
    Write-Host ''
    Write-Host '====================================================' -ForegroundColor Cyan
    Write-Host '         BEACN Windows Agent Installer' -ForegroundColor Cyan
    Write-Host "                 Version $Version" -ForegroundColor Cyan
    Write-Host '====================================================' -ForegroundColor Cyan
    Write-Host ''
}

function Write-Step {
    param(
        [string]$Number,
        [string]$Message
    )

    Write-Host ''
    Write-Host "[$Number] $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)

    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-WarningMessage {
    param([string]$Message)

    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Fail {
    param([string]$Message)

    Write-Host "[ERROR] $Message" -ForegroundColor Red
    throw $Message
}

function Test-RequiredSourceFiles {
    foreach ($Item in $RequiredFiles) {
        $Path = Join-Path $SourceDir $Item

        if (-not (Test-Path $Path)) {
            Fail "Required installation file is missing: $Item"
        }
    }
}

function Get-InstallMode {
    if ($Mode -ne 'Auto') {
        return $Mode.ToLowerInvariant()
    }

    $Service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

    if ($Service) {
        return 'upgrade'
    }

    if (Test-Path $InstallDir) {
        return 'repair'
    }

    return 'install'
}

function Stop-ExistingService {
    $Service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

    if (-not $Service) {
        return
    }

    if ($Service.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force
        $Service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
}

function Remove-ExistingServiceRegistration {
    $Wrapper = Join-Path $InstallDir 'BeacnAgentService.exe'

    if (Test-Path $Wrapper) {
        & $Wrapper stop 2>$null | Out-Null
        & $Wrapper uninstall 2>$null | Out-Null
    } elseif (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        sc.exe delete $ServiceName | Out-Null
    }
}

function Backup-ExistingInstallation {
    if (-not (Test-Path $InstallDir)) {
        return $null
    }

    $Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $BackupDir = "$InstallDir.backup-$Timestamp"

    Copy-Item `
        -Path $InstallDir `
        -Destination $BackupDir `
        -Recurse `
        -Force

    return $BackupDir
}

function Preserve-Configuration {
    $TemporaryConfig = Join-Path $env:TEMP 'beacn-agent-config.json'

    if (Test-Path $ConfigPath) {
        Copy-Item $ConfigPath $TemporaryConfig -Force
        return $TemporaryConfig
    }

    return $null
}
function Update-LegacyConfiguration {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return
    }

    try {
        $Config = Get-Content $Path -Raw | ConvertFrom-Json
    }
    catch {
        Write-WarningMessage "Unable to read existing configuration. Using defaults."
        return
    }

    $Changed = $false

    #
    # iperf3
    #

    if (
        -not $Config.PSObject.Properties['iperf_path'] -or
        $Config.iperf_path -match 'NetworkDashboardAgent'
    ) {
        if ($Config.PSObject.Properties['iperf_path']) {
            $Config.iperf_path = 'iperf3.exe'
        }
        else {
            $Config | Add-Member `
                -NotePropertyName iperf_path `
                -NotePropertyValue 'iperf3.exe'
        }

        $Changed = $true
    }

    #
    # Hardware helper
    #

    if (
        -not $Config.PSObject.Properties['hardware_helper_path'] -or
        $Config.hardware_helper_path -match 'NetworkDashboardAgent'
    ) {
        if ($Config.PSObject.Properties['hardware_helper_path']) {
            $Config.hardware_helper_path = 'hardware-helper.exe'
        }
        else {
            $Config | Add-Member `
                -NotePropertyName hardware_helper_path `
                -NotePropertyValue 'hardware-helper.exe'
        }

        $Changed = $true
    }

    #
    # Helper timeout
    #

    if (-not $Config.PSObject.Properties['hardware_helper_timeout_seconds']) {

        $Config | Add-Member `
            -NotePropertyName hardware_helper_timeout_seconds `
            -NotePropertyValue 8

        $Changed = $true
    }

    #
    # Hardware cache
    #

    if (-not $Config.PSObject.Properties['hardware_cache_seconds']) {

        $Config | Add-Member `
            -NotePropertyName hardware_cache_seconds `
            -NotePropertyValue 30

        $Changed = $true
    }

    if ($Changed) {

        $Config |
            ConvertTo-Json -Depth 10 |
            Set-Content $Path -Encoding UTF8

        Write-Success "Configuration migrated to the latest format."
    }
}
function Install-ApplicationFiles {
    param(
        [string]$PreservedConfig
    )

    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
    }

    New-Item `
        -ItemType Directory `
        -Path $InstallDir `
        -Force |
        Out-Null

    Copy-Item `
        -Path (Join-Path $SourceDir '*') `
        -Destination $InstallDir `
        -Recurse `
        -Force

    if ($PreservedConfig -and (Test-Path $PreservedConfig)) {
        Copy-Item $PreservedConfig $ConfigPath -Force
		Update-LegacyConfiguration -Path $ConfigPath
        Remove-Item $PreservedConfig -Force -ErrorAction SilentlyContinue
        Write-Success 'Existing configuration retained.'
    } else {
		Update-LegacyConfiguration -Path $ConfigPath
        Write-Success 'Default configuration installed.'
    }
}

function Install-Service {
    $Wrapper = Join-Path $InstallDir 'BeacnAgentService.exe'

    & $Wrapper install

    if ($LASTEXITCODE -ne 0) {
        Fail 'WinSW service installation failed.'
    }

    Set-Service `
        -Name $ServiceName `
        -StartupType Automatic
}

function Configure-Firewall {
    Get-NetFirewallRule `
        -DisplayName 'BEACN Agent API' `
        -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule

    Get-NetFirewallRule `
        -DisplayName 'BEACN iperf3' `
        -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule

    New-NetFirewallRule `
        -DisplayName 'BEACN Agent API' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8767 `
        -Profile Any |
        Out-Null

    New-NetFirewallRule `
        -DisplayName 'BEACN iperf3' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 5201 `
        -Profile Any |
        Out-Null
}

function Start-AgentService {
    Start-Service -Name $ServiceName

    $Service = Get-Service -Name $ServiceName
    $Service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
}

function Test-AgentHealth {
    for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
        try {
            $Response = Invoke-RestMethod `
                -Uri $HealthUrl `
                -Method Get `
                -TimeoutSec 5

            if ($Response.ok -eq $true) {
                return $Response
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    return $null
}

function Restore-Backup {
    param(
        [string]$BackupDir
    )

    Write-WarningMessage 'Restoring the previous BEACN Agent installation.'

    Stop-ExistingService
    Remove-ExistingServiceRegistration

    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
    }

    if ($BackupDir -and (Test-Path $BackupDir)) {
        Copy-Item `
            -Path $BackupDir `
            -Destination $InstallDir `
            -Recurse `
            -Force

        $Wrapper = Join-Path $InstallDir 'BeacnAgentService.exe'

        if (Test-Path $Wrapper) {
            & $Wrapper install | Out-Null
            Start-Service -Name $ServiceName
        }
    }
}

Write-Banner
Test-RequiredSourceFiles

$InstallMode = Get-InstallMode

Write-Step '1/8' 'Checking installation state'
Write-Success "Mode selected: $InstallMode"

Write-Step '2/8' 'Checking installation package'
Write-Success 'Required files are present.'

Write-Step '3/8' 'Stopping any existing BEACN Agent service'
Stop-ExistingService
Write-Success 'Existing service stopped or not present.'

$BackupDir = $null
$PreservedConfig = $null

try {
    Write-Step '4/8' 'Preparing installation files'

    $BackupDir = Backup-ExistingInstallation
    $PreservedConfig = Preserve-Configuration

    Remove-ExistingServiceRegistration

    Install-ApplicationFiles `
        -PreservedConfig $PreservedConfig

    Write-Success 'Application files installed.'

    Write-Step '5/8' 'Installing Windows service'
    Install-Service
    Write-Success 'Windows service installed.'

    Write-Step '6/8' 'Configuring Windows Firewall'
    Configure-Firewall
    Write-Success 'Firewall rules configured.'

    Write-Step '7/8' 'Starting BEACN Agent'
    Start-AgentService
    Write-Success 'BEACN Agent service is running.'

    Write-Step '8/8' 'Verifying agent health'
    $Health = Test-AgentHealth

    if (-not $Health) {
        throw 'The BEACN Agent health check failed.'
    }

    Write-Success 'Agent health check passed.'

    if ($BackupDir -and (Test-Path $BackupDir)) {
        Remove-Item $BackupDir -Recurse -Force
    }

    Write-Host ''
    Write-Host '====================================================' -ForegroundColor Green
    Write-Host '       BEACN Windows Agent installation complete' -ForegroundColor Green
    Write-Host '====================================================' -ForegroundColor Green
    Write-Host ''
    Write-Host "Mode:      $InstallMode"
    Write-Host "Version:   $Version"
    Write-Host "Service:   $ServiceName"
    Write-Host "Install:   $InstallDir"
    Write-Host "Health:    $HealthUrl"
    Write-Host "iperf3:    $($Health.iperf3)"
    Write-Host "Hardware:  $($Health.hardware)"
    Write-Host ''
} catch {
    Write-WarningMessage $_.Exception.Message

    Restore-Backup `
        -BackupDir $BackupDir

    Fail 'Installation failed. The previous installation was restored where possible.'
}
