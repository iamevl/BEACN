#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this uninstaller with sudo."
  exit 1
fi

systemctl disable --now beacn-agent.service 2>/dev/null || true
rm -f /etc/systemd/system/beacn-agent.service
systemctl daemon-reload
rm -rf /opt/beacn-agent

echo "Agent removed."
echo "Configuration was retained at /etc/beacn-agent/config.json"
