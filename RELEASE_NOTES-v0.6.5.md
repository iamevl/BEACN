# Network Dashboard v0.6.5

## Tooltip Time Hotfix

Fixes chart hover tooltips displaying `Unknown time`.

The telemetry API returns each sample time in the `created_at` field. The v0.6.4
frontend incorrectly looked for `timestamp`.

### Changed
- Chart tooltips now read `point.created_at`.
- Exact local date and time are displayed for each historical sample.
- Existing metrics history is preserved.
