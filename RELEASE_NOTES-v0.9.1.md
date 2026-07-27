# v0.9.1 Windows Agent Core Refactor

- Removes the pywin32 service implementation.
- Introduces the `network_dashboard_agent` package.
- Separates configuration, state, collectors, runtime supervision and HTTP API.
- Adds `/info` for agent diagnostics and future update compatibility.
- Retains `/`, `/status`, `/hardware` and `/health` behaviour from the supplied source.
- Adds `agent/run.ps1` for foreground verification.
- Does not yet install a Windows service. WinSW and PyInstaller follow next.

## Source note

The supplied Windows agent source does not contain `/docker` or a dedicated
`/services` endpoint. Those are therefore not claimed or fabricated by this
refactor. They can be incorporated from the intended v0.8 Windows branch before
or during the packaging milestone.
