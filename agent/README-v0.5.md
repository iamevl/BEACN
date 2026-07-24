# Network Dashboard Windows Agent v0.5

Adds LibreHardwareMonitor-based hardware telemetry without requiring the
LibreHardwareMonitor desktop application.

## Developer build

1. Install the .NET 8 SDK.
2. Run `pwsh .\build-helper.ps1`.
3. Ensure `iperf3.exe` and its DLL files are present.
4. Run `.\install.ps1` from an elevated PowerShell.

## Endpoints

- `/status` full agent payload
- `/hardware` fresh hardware reading
- `/health` service health

## Packaging

For a GitHub Release, include the self-contained `hardware-helper.exe` produced
by `build-helper.ps1`. End users do not need the .NET runtime or SDK.
