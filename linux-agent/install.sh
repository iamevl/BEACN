#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/network-dashboard-agent"
CONFIG_DIR="/etc/network-dashboard-agent"
SERVICE_FILE="/etc/systemd/system/network-dashboard-agent.service"

echo "Installing Network Dashboard Linux Agent..."

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip iperf3
else
  echo "This first installer supports Debian, Ubuntu and Raspberry Pi OS."
  echo "Install Python 3, python3-venv and iperf3, then rerun it."
  exit 1
fi

mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}"
install -m 0755 "${SCRIPT_DIR}/agent_service.py" "${INSTALL_DIR}/agent_service.py"
install -m 0644 "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
install -m 0644 "${SCRIPT_DIR}/network-dashboard-agent.service" "${SERVICE_FILE}"

if [[ ! -f "${CONFIG_DIR}/config.json" ]]; then
  install -m 0644 "${SCRIPT_DIR}/config.example.json" "${CONFIG_DIR}/config.json"
  echo "Created ${CONFIG_DIR}/config.json"
else
  echo "Keeping existing ${CONFIG_DIR}/config.json"
fi

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/python" -m pip install --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

systemctl daemon-reload
systemctl enable --now network-dashboard-agent.service

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 8767/tcp comment "Network Dashboard Agent" || true
  ufw allow 5201/tcp comment "Network Dashboard iperf3" || true
fi

echo
echo "Linux agent installed."
echo "Status: systemctl status network-dashboard-agent --no-pager"
echo "API:    http://$(hostname -I | awk '{print $1}'):8767/status"
echo "Docker: http://$(hostname -I | awk '{print $1}'):8767/docker"
