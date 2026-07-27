# Network Dashboard v0.6.8

## JavaScript Modules

This release splits the 1,000+ line frontend application script into smaller,
purpose-focused files while preserving the established execution order.

### New structure

- `static/js/core.js`
  - Shared state, DOM references, formatting and utility helpers.
- `static/js/hardware.js`
  - Hardware sensor processing and device panel rendering.
- `static/js/charts.js`
  - Telemetry loading, chart drawing, tooltips and range controls.
- `static/js/devices.js`
  - Device details, history, live polling and discovery refresh.
- `static/js/network.js`
  - API POST helper, UI event wiring, ping, port scan and iperf actions.

### Preserved

- Existing dashboard appearance and behaviour.
- Hardware monitoring and historical charts.
- Tooltip timestamps.
- Live polling and device discovery.
- Version-based browser cache busting.
- Docker static asset packaging fix from v0.6.7.

This is a structural release. It intentionally adds no new user-facing feature.
