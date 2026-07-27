# Changelog

## v0.6.2 - Stability & Polish

### Fixed
- Prevented competing SQLite writers from producing intermittent database lock errors.
- Cleared stale collector error messages after successful collection.

### Changed
- Enabled SQLite WAL mode and a longer busy timeout.
- Smoothed memory-history rendering with a three-sample moving average.
- Added a project roadmap and known-limitations document.


## v0.6.1 - Hardware Polish

### Fixed
- DIMM cards now show only real module temperatures
- Missing historical values are no longer plotted as zero
- CPU temperature and power charts no longer begin with misleading zero lines
- GPU history now reports an idle state when activity is negligible

### Changed
- Hardware charts use dynamic, padded vertical ranges
- Historical metrics section renamed to Performance History
- Temperature chart labels now include °C

## v0.6.0 - Historical Metrics

### Added
- Background agent metrics collector
- Historical CPU temperature, power and clock storage
- Historical GPU load, temperature and power storage
- Selectable 1 hour, 6 hour, 24 hour and 7 day chart ranges
- Hardware history charts
- Automatic SQLite schema migration and retention pruning

### Changed
- Telemetry point limit increased to 1000
- Dashboard stage renamed to Historical Metrics


## v0.6.6 - Frontend Foundation
- Extracted inline CSS into a static stylesheet.
- Extracted inline JavaScript into a static application script.
- Added versioned static asset URLs to avoid stale browser caches.
- Preserved existing dashboard behaviour.


## v0.6.7 - Static Assets Hotfix
- Added `COPY static ./static` to the Dockerfile.
- Restored CSS styling and JavaScript functionality after the v0.6.6 frontend split.


## v0.6.8 - JavaScript Modules
- Replaced `static/js/app.js` with `core.js`, `hardware.js`, `charts.js`,
  `devices.js` and `network.js`.
- Updated the template to load the modules in dependency order.
- Preserved all existing frontend behaviour.


## v0.7.0 - Docker Monitoring
- Added local Docker Engine overview and container inventory.
- Added live container CPU, memory, network, uptime and restart metrics.
- Added Docker health-check and published-port presentation.
- Added Docker SDK dependency and read-only socket mount.
- Added dedicated `docker.js` frontend module.


## v0.7.1 - Linux Agent
- Added Linux system telemetry and Docker monitoring agent.
- Added systemd install/uninstall scripts.
- Added selected-device Docker routing through agent `/docker`.
- Added per-device Docker frontend caching and presentation.
