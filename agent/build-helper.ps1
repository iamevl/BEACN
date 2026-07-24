#Requires -Version 7.0

$ErrorActionPreference = 'Stop'
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Project = Join-Path $BaseDir 'hardware-helper\HardwareHelper.csproj'
$Output = Join-Path $BaseDir 'hardware-helper\publish'

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw '.NET 8 SDK is required to build the helper.'
}

Remove-Item $Output -Recurse -Force -ErrorAction SilentlyContinue

dotnet publish $Project `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    --output $Output

if ($LASTEXITCODE -ne 0) {
    throw 'Hardware helper build failed.'
}

Copy-Item `
    (Join-Path $Output 'hardware-helper.exe') `
    (Join-Path $BaseDir 'hardware-helper.exe') `
    -Force

Write-Host 'Built hardware-helper.exe' -ForegroundColor Green
