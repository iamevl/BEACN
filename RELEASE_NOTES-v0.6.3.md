# BEACN v0.6.3

## Live Refresh Hotfix

This release fixes the browser error:

`Unexpected token '<', '<!doctype ...' is not valid JSON`

The live two-second agent refresh and the background metrics collector could both
attempt to update SQLite at the same time. Flask then returned its HTML error page,
which the browser tried to parse as JSON.

### Changes

- Uses the same database write lock for live/manual agent refreshes and background collection.
- Keeps read-only device requests concurrent.
- Gives the frontend a useful HTTP error instead of a raw JSON parsing message.
- Preserves the existing SQLite history database.
