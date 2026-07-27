# Network Dashboard v0.6.1 - Hardware Polish

This maintenance iteration improves the v0.6 Historical Metrics interface.

## Changes

- Filters DIMM metadata such as thermal limits and sensor resolution
- Treats null telemetry as missing data instead of zero
- Dynamically scales CPU temperature, CPU power, and memory charts
- Shows `GPU is currently idle` instead of drawing a meaningless near-zero graph
- Renames Historical metrics to Performance history

## Upgrade

Copy this release over the existing v0.6 feature branch, rebuild the Docker image, and recreate the container.
