# Network Dashboard v0.6 Historical Metrics

Copy this release over the v0.5.0 checkout on the `feature/v0.6-storage-health` branch, rebuild the Docker image, and test before committing.

## Deploy

```bash
cd /opt/network-dashboard
cp -a /tmp/network-dashboard-v0.6-historical-metrics/. ./
docker compose build --no-cache
docker compose up -d --force-recreate
docker logs --tail 100 network-dashboard
```

Metrics are collected every 15 seconds and retained for 30 days in the existing SQLite database under `data/`.
