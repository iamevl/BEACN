#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this uninstaller with sudo."
  exit 1
fi

systemctl disable --now network-dashboard-agent.service 2>/dev/null || true
rm -f /etc/systemd/system/network-dashboard-agent.service
systemctl daemon-reload
rm -rf /opt/network-dashboard-agent

echo "Agent removed."
echo "Configuration was retained at /etc/network-dashboard-agent/config.json"
