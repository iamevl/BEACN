#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# BEACN Linux Agent Installer
###############################################################################

VERSION="0.9.3"

INSTALL_DIR="/opt/beacn-agent"
CONFIG_DIR="/etc/beacn-agent"
SERVICE_NAME="beacn-agent.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

LEGACY_INSTALL_DIR="/opt/beacn-node"
LEGACY_CONFIG_DIR="/etc/beacn-node"
LEGACY_SERVICE_NAME="beacn-node.service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

###############################################################################
# Console helpers
###############################################################################

banner() {
    echo
    echo "===================================================="
    echo "           BEACN Linux Agent Installer"
    echo "                  Version ${VERSION}"
    echo "===================================================="
    echo
}

step() {
    echo
    echo "[$1] $2"
}

success() {
    echo "[OK] $1"
}

warn() {
    echo "[OK] $1"
}

fail() {
    echo "[OK] $1"
    exit 1
}

###############################################################################
# Safety checks
###############################################################################

if [[ ${EUID} -ne 0 ]]; then
    fail "Please run this installer with sudo."
fi

banner
###############################################################################
# Installation detection
###############################################################################

INSTALL_MODE="fresh"

if [[ -d "${INSTALL_DIR}" ]] || systemctl list-unit-files \
    | grep -q "^${SERVICE_NAME}"; then
    INSTALL_MODE="upgrade"
elif [[ -d "${LEGACY_INSTALL_DIR}" ]] \
    || [[ -d "${LEGACY_CONFIG_DIR}" ]] \
    || systemctl list-unit-files | grep -q "^${LEGACY_SERVICE_NAME}"; then
    INSTALL_MODE="migration"
fi

step "1/8" "Checking existing installation"

case "${INSTALL_MODE}" in
    fresh)
        success "No existing BEACN Linux Agent installation detected."
        ;;
    upgrade)
        success "Existing BEACN Linux Agent installation detected."
        ;;
    migration)
        success "Legacy BEACN Node installation detected."
        ;;
    *)
        fail "Unable to determine installation mode."
        ;;
esac
###############################################################################
# Installer functions
###############################################################################

install_packages() {
    step "2/8" "Checking operating-system dependencies"

    if ! command -v apt-get >/dev/null 2>&1; then
        fail "This installer currently supports Debian, Ubuntu and Raspberry Pi OS."
    fi

    local required_packages=(
        python3
        python3-venv
        python3-pip
        iperf3
        curl
    )

    local missing_packages=()
    local package

    for package in "${required_packages[@]}"; do
        if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null \
            | grep -q "install ok installed"; then
            missing_packages+=("${package}")
        fi
    done

    if [[ ${#missing_packages[@]} -eq 0 ]]; then
        success "All operating-system dependencies are already installed."
        return
    fi

    warn "Missing packages: ${missing_packages[*]}"

    if ! apt-get update; then
        warn "apt-get update reported errors from one or more configured repositories."
        warn "Continuing with the existing package indexes."
    fi

    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y \
        "${missing_packages[@]}"; then
        fail "Required operating-system packages could not be installed."
    fi

    success "Operating-system dependencies installed."
}


prepare_directories() {
    step "3/8" "Preparing installation directories"

    mkdir -p "${INSTALL_DIR}"
    mkdir -p "${CONFIG_DIR}"

    success "Installation directories prepared."
}


preserve_or_migrate_config() {
    step "4/8" "Preparing configuration"

    if [[ -f "${CONFIG_DIR}/config.json" ]]; then
        success "Existing ${CONFIG_DIR}/config.json retained."
        return
    fi

    if [[ "${INSTALL_MODE}" == "migration" ]] \
        && [[ -f "${LEGACY_CONFIG_DIR}/config.json" ]]; then
        install -m 0644 \
            "${LEGACY_CONFIG_DIR}/config.json" \
            "${CONFIG_DIR}/config.json"

        success "Legacy configuration migrated."
        return
    fi

    install -m 0644 \
        "${SCRIPT_DIR}/config.example.json" \
        "${CONFIG_DIR}/config.json"

    success "Default configuration created."
}


install_application_files() {
    step "5/8" "Installing BEACN Linux Agent files"

    install -m 0755 \
        "${SCRIPT_DIR}/agent_service.py" \
        "${INSTALL_DIR}/agent_service.py"

    install -m 0644 \
        "${SCRIPT_DIR}/requirements.txt" \
        "${INSTALL_DIR}/requirements.txt"

    install -m 0644 \
        "${SCRIPT_DIR}/beacn-agent.service" \
        "${SERVICE_FILE}"

    success "Application and service files installed."
}


install_python_environment() {
    step "6/8" "Preparing Python environment"

    if [[ ! -x "${INSTALL_DIR}/venv/bin/python" ]]; then
        python3 -m venv "${INSTALL_DIR}/venv"
        success "Python virtual environment created."
    else
        success "Existing Python virtual environment retained."
    fi

    "${INSTALL_DIR}/venv/bin/python" -m pip install \
        --upgrade pip

    "${INSTALL_DIR}/venv/bin/pip" install \
        --upgrade \
        -r "${INSTALL_DIR}/requirements.txt"

    success "Python dependencies installed."
}


configure_firewall() {
    if command -v ufw >/dev/null 2>&1 \
        && ufw status | grep -q "Status: active"; then
        ufw allow 8767/tcp comment "BEACN Agent" || true
        ufw allow 5201/tcp comment "BEACN iperf3" || true
        success "UFW rules checked."
    fi
}


start_new_service() {
    step "7/8" "Starting BEACN Linux Agent"

    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}"

    success "BEACN Linux Agent service started."
}


health_check() {
    step "8/8" "Verifying agent health"

    local attempt
    local health_url="http://127.0.0.1:8767/health"

    for attempt in {1..30}; do
        if systemctl is-active --quiet "${SERVICE_NAME}"; then
            break
        fi

        sleep 1
    done

    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
        warn "The BEACN Linux Agent service did not become active."
        return 1
    fi

    sleep 2

    if curl \
        --silent \
        --show-error \
        --fail \
        --max-time 10 \
        "${health_url}" \
        >/tmp/beacn-agent-health.json; then

        success "Agent health check passed."
        return 0
    fi

    warn "The service is active, but the health endpoint did not respond successfully."
    return 1
}


restore_legacy_service() {
    warn "Restoring the legacy BEACN Node service."

    systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true

    if systemctl list-unit-files \
        | grep -q "^${LEGACY_SERVICE_NAME}"; then
        systemctl enable --now "${LEGACY_SERVICE_NAME}" || true
    fi
}


complete_legacy_migration() {
    if [[ "${INSTALL_MODE}" != "migration" ]]; then
        return
    fi

    step "Migration" "Retiring legacy BEACN Node installation"

    systemctl disable --now "${LEGACY_SERVICE_NAME}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${LEGACY_SERVICE_NAME}"
    systemctl daemon-reload

    local archive_path="${LEGACY_INSTALL_DIR}.migrated-$(date +%Y%m%d-%H%M%S)"

    if [[ -d "${LEGACY_INSTALL_DIR}" ]]; then
        mv "${LEGACY_INSTALL_DIR}" "${archive_path}"
        success "Legacy installation archived at ${archive_path}"
    fi

    success "Legacy service retired."
}


print_summary() {
    local host_ip
    host_ip="$(hostname -I | awk '{print $1}')"

    echo
    echo "===================================================="
    echo "       BEACN Linux Agent installation complete"
    echo "===================================================="
    echo
    echo "Mode:     ${INSTALL_MODE}"
    echo "Version:  ${VERSION}"
    echo "Hostname: $(hostname)"
    echo "Service:  ${SERVICE_NAME}"
    echo "Status:   systemctl status beacn-agent --no-pager"
    echo "API:      http://${host_ip}:8767/status"
    echo "Hardware: http://${host_ip}:8767/hardware"
    echo "Docker:   http://${host_ip}:8767/docker"
    echo
}


###############################################################################
# Main
###############################################################################


install_packages
prepare_directories
preserve_or_migrate_config
install_application_files
install_python_environment
configure_firewall
if [[ "${INSTALL_MODE}" == "migration" ]]; then
    step "Handover" "Stopping legacy service"

    systemctl stop "${LEGACY_SERVICE_NAME}" 2>/dev/null || true

    success "Legacy service stopped for final handover."
fi
start_new_service

if health_check; then
    complete_legacy_migration
    print_summary
else
    warn "The new BEACN Linux Agent did not pass its health check."

    if [[ "${INSTALL_MODE}" == "migration" ]]; then
        restore_legacy_service
    fi

    echo
    systemctl status "${SERVICE_NAME}" --no-pager || true
    echo
    journalctl -u "${SERVICE_NAME}" -n 50 --no-pager || true

    fail "Installation failed. The legacy service was left available for rollback."
fi
