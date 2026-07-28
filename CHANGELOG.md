# Changelog

## [0.10.0] - 2026-07-28

### Added
- Canonical Device and Observation domain models.
- Immutable UUID identity for inventory devices.
- Shared database schema and DeviceRepository layers.
- Canonical `/api/devices/<device_id>` endpoint.
- Observation persistence foundation.

### Changed
- Refactored database initialisation out of the Console monolith.
- Preserved legacy IP-based routes during the inventory migration.
- Updated Docker packaging for the shared BEACN module.

All notable changes to BEACN will be documented in this file.

The format loosely follows Keep a Changelog.

---

## [0.9.3] - 2026-07-27

### Added

- Docker inventory
- Linux Node improvements
- BEACN branding
- Improved repository documentation

### Changed

- Docker endpoint now responds in approximately 100 ms.
- Improved Console responsiveness.

### Fixed

- Fixed Docker inventory timeout.
- Fixed Linux Node blocking on Docker statistics collection.

