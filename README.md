# BEACN (Pronounced Beacon)

> Modern, lightweight infrastructure monitoring for homelabs, small businesses and edge deployments.

![Version](https://img.shields.io/badge/version-0.9.3-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-green)
![Python](https://img.shields.io/badge/python-3.11+-yellow)
![License](https://img.shields.io/badge/license-MIT-brightgreen)

---

## Overview

BEACN is a lightweight monitoring platform designed to provide fast visibility into servers, workstations, virtual machines and Docker hosts without requiring a heavyweight monitoring stack.
---
This project is **not affiliated with, endorsed by, or associated with BEACN (the audio hardware company)** or any other organisation using the BEACN name.

If you're looking for professional, broadcast audio products such as microphones, audio interfaces or streaming hardware, please visit their official website: [Beacn.com](https://www.beacn.com) they produce some excellent products.

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



