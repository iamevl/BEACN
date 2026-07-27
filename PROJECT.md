# Network Dashboard Project

## Released

- v0.2.0 Device Discovery
- v0.3.0 Device Intelligence
- v0.4.0 Live Monitoring
- v0.5.0 Hardware Monitoring

## In development

### v0.6 Historical Metrics

- SQLite telemetry history
- CPU temperature, power and utilisation charts
- Memory and GPU history
- Selectable time ranges
- WAL mode and serialised database writes
- Smoothed memory history

## Roadmap

- v0.7 Storage health and SMART data
- v0.8 Docker monitoring
- v0.9 Alerts and notifications
- v1.0 Network topology and polished release

## Known limitations

- Fan readings depend on motherboard controller support.
- Intel integrated GPUs may not expose temperature sensors.
- Hardware-helper storage enumeration remains disabled because it can hang on some systems.

## v0.6.3
- Serialised manual/live agent refresh writes with the metrics collector.
- Prevented SQLite writer collisions during two-second live polling.
- Added clearer handling when an API endpoint returns HTML instead of JSON.


## v0.6.4
- Added exact-value hover tooltips to hardware history charts.
- Added latest-value and health chips to history cards.
- Added CPU and memory health colouring to live metric cards.
- Improved idle GPU presentation.
- Preserved the v0.6.3 SQLite concurrency fix.


## v0.6.5
- Fixed historical chart tooltip timestamps by using the API's `created_at` field.


## v0.6.6
- Began frontend modularisation.
- Moved embedded CSS to `static/css/app.css`.
- Moved embedded JavaScript to `static/js/app.js`.
- Added asset cache busting using the application version.
- This is a behaviour-preserving foundation release before deeper JavaScript module splitting.


## v0.6.7
- Fixed static CSS and JavaScript returning HTTP 404 from the Docker container.
- Added the new `static/` directory to the Docker image build.


## v0.6.8
- Split the monolithic frontend JavaScript into five purpose-focused files.
- Preserved classic-script loading order to minimise refactor risk.
- Established dedicated homes for core utilities, hardware rendering, charts,
  device lifecycle and network tools.
- Prepared the frontend for a standalone Docker Monitoring module.


## v0.7.0
- Added read-only local Docker Engine monitoring.
- Added `static/js/docker.js` as the first feature built on the modular frontend.
- Added container health, CPU, memory, uptime, restarts, network totals and ports.
- Added Docker socket access to the Compose deployment.
- Deliberately omitted container control actions from the initial release.


## v0.7.1
- Added the first Network Dashboard Linux Agent.
- Added Debian, Ubuntu and Raspberry Pi OS systemd installer.
- Made Docker monitoring follow the selected agent-enabled device.
- Established the cross-platform agent contract: `/status`, `/hardware`,
  `/docker` and `/health`.
- Preserved the local Docker endpoint as a transitional fallback.
- Future direction: a universal installer that detects OS and CPU architecture.
