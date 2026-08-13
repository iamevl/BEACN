# BEACN (Pronounced Beacon)

> BEACN is an open source infrastructure discovery, inventory and observability platform.

![Version](https://img.shields.io/badge/version-0.9.3-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-green)
![Python](https://img.shields.io/badge/python-3.11+-yellow)
![License](https://img.shields.io/badge/license-MIT-brightgreen)

---

## Overview

BEACN is a lightweight monitoring platform designed to provide fast visibility into servers, workstations, virtual machines and Docker hosts without requiring a heavyweight monitoring stack.

---
> [!NOTE]
> Looking for professional, broadcast audio products such as microphones, mixers or streaming hardware? Please visit BEACN Audio. They produce some excellent products.
>
> This repository is **not affiliated with BEACN Audio**.
>
> Their official website is:
> https://www.beacn.com

---
Unlike enterprise monitoring suites that require databases, collectors and complex configuration, BEACN is designed to deploy in minutes while remaining extensible enough to grow into a full monitoring platform.

Current focus areas include:

- Device discovery
- System health
- Hardware inventory
- Docker monitoring
- Network diagnostics
- Performance testing
- Remote management

<img width="1397" height="865" alt="Screenshot 1" src="https://github.com/user-attachments/assets/f07c3992-775b-4912-9c1a-7e5618be74cf" />
---

## Architecture

```
                  +----------------------+
                  |   BEACN Console      |
                  |  Flask Web UI/API    |
                  +----------+-----------+
                             |
                HTTP / REST API
                             |
        +--------------------+--------------------+
        |                                         |
+-------+--------+                      +----------+-------+
| Windows Node   |                      | Linux Node       |
| BeacnAgent.exe |                      | beacn-node       |
+-------+--------+                      +----------+-------+
        |                                          |
  Windows APIs                            Linux / Docker
        |                                          |
 Hardware / Services                 Hardware / Containers
```

---

## Features

### Console

- Responsive web interface
- Device inventory
- Hardware overview
- Network diagnostics
- Docker overview
- iperf3 integration
- REST API

### Windows Node

- CPU
- Memory
- Disk usage
- Network interfaces
- Windows services
- Hardware inventory
- iperf3 support

### Linux Node

- CPU
- Memory
- Disk usage
- Network interfaces
- Docker inventory
- Docker health
- iperf3 support

---

## Docker Support

Current release includes:

- Container inventory
- Running / stopped status
- Health status
- Image information
- Restart counts
- Port mappings
- Labels

Version 0.9.3 reduced Docker inventory response time from approximately **29 seconds** to around **100 milliseconds** by removing synchronous per-container statistics collection.

Live Docker telemetry (CPU, memory, network and disk) is planned for the next major release using a dedicated telemetry API.

---

## Repository Layout

```
BEACN/

├── console/
│   Flask web console
│
├── node-linux/
│   Linux monitoring agent
│
├── node-windows/
│   Windows monitoring agent
│
├── version.py
│
└── docker-compose.yml
```

---

## Installation

### Console

```bash
git clone https://github.com/iamevl/BEACN.git

cd BEACN

cp .env.example .env
```

Edit `.env` and set the required `NETWORK_SUBNET` value to the IPv4 CIDR that
BEACN is authorised to monitor. This setting permits active discovery and
network probing, so confirm the scope carefully. BEACN will refuse to start if
the value is missing, blank or invalid.

Validate the resolved configuration before starting BEACN:

```bash
docker compose config
```

Review the resolved `NETWORK_SUBNET`, then start the Console:

```bash
docker compose up -d
```

The Console is available on:

```
http://<server>:8766
```

---

### Linux Node

```
sudo ./install.sh
```

---

### Windows Node

Run:

```
BeacnAgent.exe
```

or install it as a Windows Service.

---

## Current Status

| Component | Status |
|-----------|--------|
| Console | ✅ Stable |
| Windows Node | ✅ Stable |
| Linux Node | ✅ Stable |
| Docker Inventory | ✅ Stable |
| Docker Telemetry | 🚧 In Progress |
| Device Discovery | 🚧 Planned |
| Historical Metrics | 🚧 Planned |
| Alerts | 🚧 Planned |
| Authentication | 🚧 Planned |

---

## Roadmap

### v0.10

- Dedicated Docker telemetry API
- Live CPU & Memory metrics
- Network throughput
- Disk I/O
- Faster dashboard refresh

### v0.11

- Historical metrics
- Graphing
- Alert engine
- Notification framework

### v1.0

- Auto-discovery
- Plugin architecture
- SNMP
- UPS monitoring
- Switch monitoring
- Multi-site support

### Optional management credential encryption

The management-source persistence foundation remains locked when no
credential encryption key is configured. Normal monitoring continues and no
plaintext fallback is used.

For production, set `BEACN_ENCRYPTION_KEY_FILE` to a read-only mounted secret
file. The file contains URL-safe Fernet keys, one per non-empty line. The first
key encrypts new credentials and remaining keys decrypt legacy records. As a
fallback, `BEACN_ENCRYPTION_KEY` supplies the active key and
`BEACN_ENCRYPTION_LEGACY_KEYS` supplies comma-separated legacy keys. Keep these
keys separate from SQLite backups and from `BEACN_SECRET_KEY`.

Management sources use application-validated canonical device or
infrastructure-object identities. Participant deletion cleanup is deferred;
the repository detects orphaned sources and excludes them from future
collection eligibility until explicit cleanup is implemented.

---

## Philosophy

BEACN aims to be:

- Lightweight
- Fast
- Easy to deploy
- Open
- Extensible
- Homelab friendly
- Enterprise capable

---

## Contributing

Issues, suggestions and pull requests are welcome.

If you discover a bug or have an idea for a feature, please open an issue.

---

## License

MIT License

---

Built with ❤️ for homelabs, labs and small infrastructure teams.

