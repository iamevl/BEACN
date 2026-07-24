# Network Dashboard v1

A small self-hosted LAN dashboard providing:

- Automatic Nmap host discovery
- Device dropdown
- Hostname, MAC and vendor display where available
- Ping
- Top-100 TCP port scan
- iperf3 availability detection on TCP 5201
- Forward and reverse iperf3 tests
- SQLite result history

## Security model

The API only accepts target IPs inside `NETWORK_SUBNET`. It does not expose a
general shell or arbitrary command execution.

Keep this dashboard LAN-only, or protect it with Cloudflare Access, a VPN, or
another authentication layer before exposing it publicly.

## Install on the Pi

1. Extract this folder to:

   `/opt/network-dashboard`

2. In that folder run:

   ```bash
   docker compose build
   docker compose up -d
   ```

3. Open:

   `http://192.168.1.***:8766`

## Manage it in Portainer

After the first deployment, Portainer will show the container normally under
Containers. To manage it as a Portainer stack, use **Stacks > Add stack >
Repository** with a Git repository containing these files.

Portainer's web editor cannot reliably build from an arbitrary host folder
because its Compose file is stored in Portainer's own stack directory. The
simplest first deployment is therefore the two Docker Compose commands above.

## Configuration

Edit `docker-compose.yml`:

- `NETWORK_SUBNET`: LAN CIDR to scan
- `APP_PORT`: dashboard port
- `IPERF_PORT`: iperf3 server port

Then run:

```bash
docker compose up -d --build
```

## Notes

- A device must run `iperf3 -s` to enable its iperf buttons.
- MAC/vendor details are most reliable when the dashboard container is on the
  same Layer-2 network, hence `network_mode: host`.
- Some devices block ICMP or scans and may not appear.
