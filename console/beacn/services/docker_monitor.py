"""Docker monitoring helpers for BEACN."""

import docker
from docker.errors import DockerException

from beacn.config import (
    DOCKER_MONITORING_ENABLED,
    DOCKER_TIMEOUT_SECONDS,
)


def docker_client():
    if not DOCKER_MONITORING_ENABLED:
        raise RuntimeError("Docker monitoring is disabled.")
    return docker.from_env(timeout=DOCKER_TIMEOUT_SECONDS)


def docker_cpu_percent(stats):
    cpu_stats = stats.get("cpu_stats") or {}
    previous = stats.get("precpu_stats") or {}

    cpu_total = (
        (cpu_stats.get("cpu_usage") or {}).get("total_usage")
        or 0
    )
    previous_total = (
        (previous.get("cpu_usage") or {}).get("total_usage")
        or 0
    )

    system_total = cpu_stats.get("system_cpu_usage") or 0
    previous_system = previous.get("system_cpu_usage") or 0

    cpu_delta = cpu_total - previous_total
    system_delta = system_total - previous_system

    online_cpus = cpu_stats.get("online_cpus")
    if not online_cpus:
        percpu = (cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or []
        online_cpus = len(percpu) or 1

    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0

    return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)


def docker_memory(stats):
    memory = stats.get("memory_stats") or {}

    usage = int(memory.get("usage") or 0)
    limit = int(memory.get("limit") or 0)

    cache = int(
        (memory.get("stats") or {}).get("inactive_file")
        or (memory.get("stats") or {}).get("cache")
        or 0
    )

    working_set = max(0, usage - cache)
    percent = (working_set / limit * 100.0) if limit else 0.0

    return working_set, limit, round(percent, 2)


def docker_network_totals(stats):
    networks = stats.get("networks") or {}

    received = sum(
        int(item.get("rx_bytes") or 0)
        for item in networks.values()
    )

    transmitted = sum(
        int(item.get("tx_bytes") or 0)
        for item in networks.values()
    )

    return received, transmitted

def docker_ports(attrs):
    ports = ((attrs.get("NetworkSettings") or {}).get("Ports") or {})
    output = []

    for container_port, bindings in ports.items():
        if not bindings:
            output.append(container_port)
            continue

        for binding in bindings:
            host_ip = binding.get("HostIp") or "0.0.0.0"
            host_port = binding.get("HostPort") or ""
            output.append(f"{host_ip}:{host_port} -> {container_port}")

    return output


def docker_container_summary(container):
    container.reload()

    attrs = container.attrs or {}
    state = attrs.get("State") or {}
    config = attrs.get("Config") or {}

    stats = container.stats(stream=False)

    memory_used, memory_limit, memory_percent = docker_memory(stats)
    network_rx, network_tx = docker_network_totals(stats)

    health = (state.get("Health") or {}).get("Status")

    image_tags = getattr(container.image, "tags", None) or []
    image_name = (
        image_tags[0]
        if image_tags
        else config.get("Image") or container.image.short_id
    )

    return {
        "id": container.short_id,
        "name": container.name,
        "image": image_name,
        "status": container.status,
        "running": bool(state.get("Running")),
        "health": health,
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "created_at": attrs.get("Created"),
        "restart_count": int(attrs.get("RestartCount") or 0),
        "cpu_percent": docker_cpu_percent(stats),
        "memory_used_bytes": memory_used,
        "memory_limit_bytes": memory_limit,
        "memory_percent": memory_percent,
        "network_rx_bytes": network_rx,
        "network_tx_bytes": network_tx,
        "ports": docker_ports(attrs),
        "labels": config.get("Labels") or {},
    }

def docker_snapshot():
    client = docker_client()

    try:
        info = client.info()

        containers = [
            docker_container_summary(container)
            for container in client.containers.list(all=True)
        ]

    finally:
        client.close()

    containers.sort(
        key=lambda item: (
            not item["running"],
            item["name"].lower(),
        )
    )

    running = sum(1 for c in containers if c["running"])
    healthy = sum(1 for c in containers if c["health"] == "healthy")
    unhealthy = sum(1 for c in containers if c["health"] == "unhealthy")

    return {
        "available": True,
        "engine": {
            "name": info.get("Name"),
            "server_version": info.get("ServerVersion"),
            "operating_system": info.get("OperatingSystem"),
            "architecture": info.get("Architecture"),
            "containers_total": len(containers),
            "containers_running": running,
            "containers_stopped": len(containers) - running,
            "containers_healthy": healthy,
            "containers_unhealthy": unhealthy,
        },
        "containers": containers,
    }
