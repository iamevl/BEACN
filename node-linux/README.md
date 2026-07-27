# BEACN Linux Agent v0.7.1

Initial target platforms:

- Debian 12
- Ubuntu
- Raspberry Pi OS
- x86-64 and ARM64

## Endpoints

- `/status`
- `/hardware`
- `/docker`
- `/health`

The service listens on TCP 8767 and supervises an iperf3 server on TCP 5201.

## Install

```bash
sudo ./install.sh
```

## Verify

```bash
systemctl status beacn-agent --no-pager
curl http://127.0.0.1:8767/health
curl http://127.0.0.1:8767/docker
```

## Configuration

`/etc/beacn-agent/config.json`

The initial release runs as root so it can read the local Docker socket. The API
is intended for trusted LANs. Set `api_token` before exposing it beyond the LAN.
