# BEACN v0.9.2 RC2

This release candidate completes the first BEACN rebrand pass while preserving compatibility with the existing Windows helper installation.

## Changes

- Product branding changed to BEACN.
- Windows Python package renamed to `beacn_agent`.
- Existing helper paths remain pointed at `C:\Program Files\NetworkDashboardAgent` during the transition.
- `config.example.json` uses portable relative helper paths for future standalone packaging.
- Generated virtual environments, caches, logs, and build output are excluded from the release archive.
- `.gitignore` expanded to keep generated files out of source control.

## Transitional compatibility

The old `NetworkDashboardAgent` installation directory is intentionally retained for v0.9.2 RC2. The path will move to the BEACN installation directory when the standalone executable and WinSW installer are introduced.
