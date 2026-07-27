# Network Dashboard v0.6.2

Stability and presentation update for the historical metrics engine.

## Changes

- Enables SQLite WAL mode.
- Adds a 30-second SQLite busy timeout.
- Serialises application database writes to prevent `database is locked` errors.
- Clears stale collector errors after a successful collection cycle.
- Smooths the memory history chart with a three-sample moving average.
- Adds `PROJECT.md` with the release roadmap and known limitations.

Existing telemetry data in `/data/network-dashboard.db` is preserved.
