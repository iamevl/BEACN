# Windows Agent v0.9.1 Core Refactor

This milestone removes the pywin32 service implementation and reorganises the
Windows agent into a package without changing its existing telemetry payloads.

## Run for verification

Open PowerShell in this folder and run:

```powershell
.\run.ps1
```

Then test:

```powershell
Invoke-RestMethod http://localhost:8767/health
Invoke-RestMethod http://localhost:8767/info
Invoke-RestMethod http://localhost:8767/status
Invoke-RestMethod http://localhost:8767/hardware
```

Stop the foreground agent with `Ctrl+C`.

## Important

This is deliberately not a Windows service build. The next milestone packages
`launcher.py` as a standalone executable and installs it using WinSW.
