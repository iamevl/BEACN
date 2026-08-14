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

### Management credential encryption key

Management credentials require a Fernet root key. Generate it once on the host,
store it outside Git with restrictive ownership and mode (normally root or the
deployment operator, mode `0600`), and back it up separately from the SQLite
database. Loss of this key makes encrypted management credentials unrecoverable.
Never substitute Flask's session key and never store the key in SQLite.

The optional Compose overlay mounts the file read-only:

```bash
sudo install -d -m 0700 /var/lib/beacn-secrets
sudo python3 -c "from cryptography.fernet import Fernet; open('/var/lib/beacn-secrets/credential-keys', 'xb').write(Fernet.generate_key() + b'\n')"
sudo chmod 0600 /var/lib/beacn-secrets/credential-keys
BEACN_ENCRYPTION_KEY_HOST_FILE=/var/lib/beacn-secrets/credential-keys \
  docker compose -f docker-compose.yml -f docker-compose.encryption.yml config
```

The exclusive-create mode makes the generation command fail rather than replace
an existing key. Substitute the actual protected path consistently in the
generation and Compose commands. The command writes the key directly to the
file and does not print it.

The file contains one Fernet key per line. The first is active for encryption;
remaining lines may decrypt legacy records during controlled rotation. BEACN
does not generate or rotate this file automatically. Without a valid readable
file, normal startup continues while credential creation and connectivity that
requires credentials remain locked.

#### Encryption-root backup and recovery

The production encryption root is stored at
`/var/lib/beacn-secrets/credential-keys`. It is required to decrypt every
management credential protected with that key. Losing it makes those
credentials unrecoverable, and replacing or rotating it without a controlled
key-ring transition makes existing ciphertext unreadable.

Keep a recovery copy separately from the BEACN host in encrypted, offline, or
otherwise secret-safe storage. An ordinary unencrypted cloud drive, application
backup, repository checkout, home directory, ZIP archive, or removable
filesystem is not an appropriate destination. SQLite backups and encryption-key
backups should remain separate but must be recoverable as a corresponding pair.

After attaching or unlocking an administrator-approved encrypted destination
with Unix permission support, an administrator may deliberately create one
recovery copy as follows. Supply the destination directory interactively; do
not substitute an ordinary backup location. These commands do not print the
key, refuse to replace an existing recovery file, and verify the copy with a
silent byte comparison:

```bash
set -eu
read -r -p "Mounted encrypted recovery directory: " BEACN_KEY_BACKUP_DIRECTORY
test -n "$BEACN_KEY_BACKUP_DIRECTORY"
test -d "$BEACN_KEY_BACKUP_DIRECTORY"
test ! -e "$BEACN_KEY_BACKUP_DIRECTORY/beacn-credential-keys"
sudo install -o root -g root -m 0600 -- \
  /var/lib/beacn-secrets/credential-keys \
  "$BEACN_KEY_BACKUP_DIRECTORY/beacn-credential-keys"
sudo cmp --silent -- \
  /var/lib/beacn-secrets/credential-keys \
  "$BEACN_KEY_BACKUP_DIRECTORY/beacn-credential-keys"
sudo stat -c 'owner=%U group=%G mode=%a bytes=%s' -- \
  "$BEACN_KEY_BACKUP_DIRECTORY/beacn-credential-keys"
unset BEACN_KEY_BACKUP_DIRECTORY
```

Where recovery inventories need to distinguish several saved roots, an
administrator may privately record the output of
`sudo sha256sum -- /var/lib/beacn-secrets/credential-keys`. This one-way,
non-secret fingerprint identifies a candidate recovery key; it cannot recover
or replace the key and should not be automatically published externally.

On a replacement or rebuilt BEACN host, do not use management credentials until
the matching database and exact saved encryption root are restored. Never
generate a replacement root when encrypted credentials already exist. After
placing the approved recovery medium in a trusted state, restore with:

```bash
set -eu
read -r -p "Exact saved encryption-root file: " BEACN_KEY_RECOVERY_FILE
test -n "$BEACN_KEY_RECOVERY_FILE"
sudo test -f "$BEACN_KEY_RECOVERY_FILE"
sudo test ! -e /var/lib/beacn-secrets/credential-keys
sudo install -d -o root -g root -m 0700 /var/lib/beacn-secrets
sudo install -o root -g root -m 0600 -- \
  "$BEACN_KEY_RECOVERY_FILE" \
  /var/lib/beacn-secrets/credential-keys
sudo cmp --silent -- \
  "$BEACN_KEY_RECOVERY_FILE" \
  /var/lib/beacn-secrets/credential-keys
sudo stat -c 'owner=%U group=%G mode=%a bytes=%s' -- \
  /var/lib/beacn-secrets/credential-keys
unset BEACN_KEY_RECOVERY_FILE
BEACN_ENCRYPTION_KEY_HOST_FILE=/var/lib/beacn-secrets/credential-keys \
  docker compose -f docker-compose.yml -f docker-compose.encryption.yml \
  up -d --no-deps --force-recreate beacn-console
docker exec beacn-console python -c \
  "from beacn.security.credentials import credential_cipher_from_environment as load; print('cipher_available=' + str(load().available).lower())"
docker exec beacn-console python -c \
  "import sqlite3; connection=sqlite3.connect('/data/beacn.db'); print(connection.execute('PRAGMA integrity_check').fetchone()[0]); print('foreign_key_violations=' + str(len(connection.execute('PRAGMA foreign_key_check').fetchall()))); connection.close()"
```

Require `cipher_available=true`, `integrity_check` output `ok`, and zero foreign
key violations. Then perform an operator-approved credential-decryption check
that reports only pass/fail and never prints plaintext. Resume management
connectivity only after that check succeeds.

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
