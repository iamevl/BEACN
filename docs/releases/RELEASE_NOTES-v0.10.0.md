# BEACN v0.10.0 - Inventory Foundation

## Summary

This release introduces the shared BEACN domain and persistence layers while preserving the current Console behaviour and existing SQLite data.

## Added

- Canonical `Device` domain model with immutable UUID identity.
- Lightweight `Observation` domain model.
- Shared database connection and migration modules.
- Device repository abstraction.
- `observations` table and supporting indexes.
- `GET /api/devices/<device_id>` canonical device endpoint.
- UUIDs returned by the existing `GET /api/devices` endpoint as `device_id`.
- Inventory round-trip test.

## Changed

- Database startup and migrations moved out of `console/app.py`.
- Console Docker image now includes the `beacn` Python package.
- Application version updated to `0.10.0` with stage `Inventory Foundation`.

## Compatibility

The existing IP-keyed `devices` table remains in place for this transitional release. Existing rows receive UUIDs automatically and no telemetry or iperf history is discarded.

Legacy IP-based endpoints remain available. New code should use immutable `device_id` values wherever possible.
