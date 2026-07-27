$ErrorActionPreference = 'Stop'
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $BaseDir

if (-not (Test-Path '.\.venv')) {
    py -3.12 -m venv .venv
}

& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Failed to install requirements.' }

if (-not (Test-Path '.\config.json')) {
    Copy-Item '.\config.example.json' '.\config.json'
}

& '.\.venv\Scripts\python.exe' '.\launcher.py'
