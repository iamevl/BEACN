# BEACN v0.7.1

## Linux Agent and Device-Aware Docker Monitoring

### Added
- Native Python Linux agent for Debian, Ubuntu and Raspberry Pi OS.
- systemd installation and automatic startup.
- Linux CPU, memory, disks, network interfaces, uptime and basic sensor data.
- Agent `/docker` endpoint with container CPU, memory, network, uptime, restart,
  image, port and health information.
- Agent-supervised iperf3 server.
- Device-aware Docker API in the dashboard.

### Changed
- The Docker tab now follows the device selected in the dashboard.
- Devices without an agent receive a clear explanatory message.
- Docker results are cached independently per selected device.
- The original local Docker endpoint remains for compatibility.

### Security
The first Linux agent runs as root so it can access `/var/run/docker.sock`.
It is intended for a trusted LAN. An API token can be configured before exposing
the agent outside that network.
