#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$Version = '0.9.1'
)

$ErrorActionPreference = 'Stop'

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $BaseDir '.build-venv'
$Python = Join-Path $VenvDir 'Scripts\python.exe'
$DistDir = Join-Path $BaseDir 'dist'
$BuildDir = Join-Path $BaseDir 'build'
$PackageDir = Join-Path $DistDir 'BeacnAgent'
$ZipName = "BeacnAgent-windows-x64-v$Version.zip"
$ZipPath = Join-Path $DistDir $ZipName
$WinSWVersion = '2.12.0'
$WinSWSource = Join-Path $BaseDir 'WinSW-x64.exe'
$WinSWUrl = "https://github.com/winsw/winsw/releases/download/v$WinSWVersion/WinSW-x64.exe"
$ServiceWrapper = Join-Path $PackageDir 'BeacnAgentService.exe'
$ServiceConfig = Join-Path $PackageDir 'BeacnAgentService.xml'

Set-Location $BaseDir

Write-Host ''
Write-Host 'BEACN Windows Node Builder' -ForegroundColor Cyan
Write-Host "Version: $Version"
Write-Host ''

$RequiredFiles = @(
    'launcher.py',
    'requirements.txt',
    'BeacnAgent.spec',
    'hardware-helper.exe',
    'iperf3.exe',
    'cygwin1.dll',
    'config.example.json',
    'cygcrypto-3.dll',
    'cygz.dll',
	'BeacnAgentService.xml',
    'install.ps1',
    'uninstall.ps1'
)

foreach ($File in $RequiredFiles) {
    if (-not (Test-Path (Join-Path $BaseDir $File))) {
        throw "Required file is missing: $File"
    }
}

if (-not (Test-Path $WinSWSource)) {
    Write-Host "Downloading WinSW v$WinSWVersion..."

    Invoke-WebRequest `
        -Uri $WinSWUrl `
        -OutFile $WinSWSource `
        -UseBasicParsing

    if (-not (Test-Path $WinSWSource)) {
        throw 'WinSW download failed.'
    }
}
if (-not (Test-Path $Python)) {
    Write-Host 'Creating isolated build environment...'
    py -3.12 -m venv $VenvDir

    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to create Python 3.12 build environment.'
    }
}

Write-Host 'Installing build dependencies...'
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw 'pip upgrade failed.'
}

& $Python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw 'Dependency installation failed.'
}

Write-Host 'Cleaning previous build output...'
Remove-Item $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $DistDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host 'Building BeacnAgent.exe...'
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    .\BeacnAgent.spec

if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller build failed.'
}

$Executable = Join-Path $PackageDir 'BeacnAgent.exe'
Copy-Item `
    $WinSWSource `
    $ServiceWrapper `
    -Force

Copy-Item `
    (Join-Path $BaseDir 'BeacnAgentService.xml') `
    $ServiceConfig `
    -Force

Copy-Item `
    (Join-Path $BaseDir 'install.ps1') `
    (Join-Path $PackageDir 'install.ps1') `
    -Force

Copy-Item `
    (Join-Path $BaseDir 'uninstall.ps1') `
    (Join-Path $PackageDir 'uninstall.ps1') `
    -Force

if (-not (Test-Path $Executable)) {
    throw "Expected executable was not created: $Executable"
}

# The example remains available, while config.json becomes the
# immediately usable default configuration.
Copy-Item `
    (Join-Path $BaseDir 'config.example.json') `
    (Join-Path $PackageDir 'config.json') `
    -Force

$VersionFile = Join-Path $PackageDir 'VERSION'
$Version | Set-Content $VersionFile -Encoding ascii

Write-Host 'Creating release ZIP...'
Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

Compress-Archive `
    -Path "$PackageDir\*" `
    -DestinationPath $ZipPath `
    -CompressionLevel Optimal

$Hash = Get-FileHash $ZipPath -Algorithm SHA256
$HashFile = "$ZipPath.sha256"
"$($Hash.Hash.ToLowerInvariant())  $ZipName" |
    Set-Content $HashFile -Encoding ascii

Write-Host ''
Write-Host 'Build completed successfully.' -ForegroundColor Green
Write-Host "Executable: $Executable"
Write-Host "Package:    $ZipPath"
Write-Host "Checksum:   $HashFile"
Write-Host "SHA256:     $($Hash.Hash)"

