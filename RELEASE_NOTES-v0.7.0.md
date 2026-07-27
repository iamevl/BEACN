# BEACN v0.7.0

## Docker Monitoring

The dashboard can now inspect the Docker Engine running on the Pi-hole host.

### Added
- Read-only Docker tab.
- Running, stopped, healthy and unhealthy container totals.
- Container name, image, state and Docker health-check status.
- Live CPU and memory utilisation.
- Container uptime and restart count.
- Network receive/transmit totals.
- Published port mappings.
- Docker Engine host and server version.
- Manual refresh control.
- Docker container count in the dashboard header.

### Deployment change
The dashboard container now mounts the host Docker socket:

```yaml
- /var/run/docker.sock:/var/run/docker.sock:ro
```

The application exposes no stop, start, restart or removal endpoints. v0.7.0 is
monitoring-only.

### Notes
The Docker socket gives the application visibility of the local Docker Engine.
Only deploy this release on a host you trust.
