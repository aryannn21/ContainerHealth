
import docker
from datetime import datetime, timezone


def get_docker_client():
    """Connect to the Docker Engine."""
    try:
        client = docker.from_env()
        client.ping()
        return client

    except Exception as e:
        print(f"Docker connection error: {e}")
        return None


def format_uptime(started_at, status):
    """Calculate how long a container has been running."""
    if status != "running" or not started_at:
        return "N/A"

    try:
        if started_at.startswith("0001-01-01"):
            return "N/A"

        normalized = started_at.replace("Z", "+00:00")

        if "." in normalized:
            timestamp_part, timezone_part = normalized.rsplit("+", 1)

            if "." in timestamp_part:
                date_part, fraction = timestamp_part.split(".", 1)
                fraction = fraction[:6].ljust(6, "0")
                normalized = f"{date_part}.{fraction}+{timezone_part}"

        started = datetime.fromisoformat(normalized)

        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        total_seconds = max(
            0,
            int((now - started.astimezone(timezone.utc)).total_seconds())
        )

        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)

        if days:
            return f"{days}d {hours}h"
        elif hours:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"

    except (ValueError, TypeError, OverflowError) as e:
        print(f"Uptime parsing error: {e}")
        return "N/A"


def get_container_metrics(container):
    """Retrieve CPU, memory and network metrics for a running container."""

    metrics = {
        "cpu_percent": None,
        "memory_usage_mb": None,
        "memory_limit_mb": None,
        "memory_percent": None,
        "network_rx_mb": None,
        "network_tx_mb": None,
    }

    if container.status != "running":
        return metrics

    try:
        stats = container.stats(stream=False)

        # CPU usage calculation.
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        cpu_usage = cpu_stats.get("cpu_usage", {})
        previous_usage = precpu_stats.get("cpu_usage", {})

        current_total = cpu_usage.get("total_usage", 0)
        previous_total = previous_usage.get("total_usage", 0)

        current_system = cpu_stats.get("system_cpu_usage", 0)
        previous_system = precpu_stats.get("system_cpu_usage", 0)

        cpu_delta = current_total - previous_total
        system_delta = current_system - previous_system

        online_cpus = cpu_stats.get("online_cpus")

        if not online_cpus:
            online_cpus = len(cpu_usage.get("percpu_usage") or [])

        if cpu_delta >= 0 and system_delta > 0 and online_cpus > 0:
            metrics["cpu_percent"] = round(
                (cpu_delta / system_delta) * online_cpus * 100,
                2,
            )

        # Memory usage and limit.
        memory_stats = stats.get("memory_stats", {})
        usage = memory_stats.get("usage")
        limit = memory_stats.get("limit")

        cache_stats = memory_stats.get("stats", {})

        inactive_file = cache_stats.get(
            "inactive_file",
            cache_stats.get("total_inactive_file", 0),
        )

        if usage is not None and limit is not None and limit > 0:
            actual_usage = max(0, usage - inactive_file)

            metrics["memory_usage_mb"] = round(
                actual_usage / (1024 ** 2), 2
            )

            metrics["memory_limit_mb"] = round(
                limit / (1024 ** 2), 2
            )

            metrics["memory_percent"] = round(
                (actual_usage / limit) * 100, 2
            )

        # Network traffic: cumulative bytes converted to MB.
        networks = stats.get("networks") or {}

        if networks:
            received = sum(
                network.get("rx_bytes", 0)
                for network in networks.values()
            )

            transmitted = sum(
                network.get("tx_bytes", 0)
                for network in networks.values()
            )

            metrics["network_rx_mb"] = round(
                received / (1024 ** 2), 3
            )

            metrics["network_tx_mb"] = round(
                transmitted / (1024 ** 2), 3
            )

    except Exception as e:
        print(f"Metrics error for {container.name}: {e}")

    return metrics


def get_containers():
    """Return container details and resource metrics."""

    client = get_docker_client()

    if client is None:
        return []

    try:
        docker_containers = client.containers.list(all=True)
        container_data = []

        for container in docker_containers:
            try:
                container.reload()

                state = container.attrs.get("State", {})
                started_at = state.get("StartedAt")
                restart_count = container.attrs.get("RestartCount", 0)
                status = state.get("Status", container.status)

                image_name = "Unknown"

                if container.image.tags:
                    image_name = container.image.tags[0]

                elif container.attrs.get("Config", {}).get("Image"):
                    image_name = container.attrs["Config"]["Image"]

                metrics = get_container_metrics(container)

                container_data.append({
                    "id": container.short_id,
                    "name": container.name,
                    "image": image_name,
                    "status": status,
                    "uptime": format_uptime(started_at, status),
                    "restart_count": restart_count,
                    **metrics,
                })

            except Exception as e:
                print(
                    f"Error retrieving container {container.name}: {e}"
                )

        return container_data

    except Exception as e:
        print(f"Error retrieving containers: {e}")
        return []


def restart_container(container_id):
    """Restart a stopped Docker container manually."""

    if not container_id:
        return False, "Container ID is required."

    client = get_docker_client()

    if client is None:
        return False, "Cannot connect to Docker Engine."

    try:
        container = client.containers.get(container_id)
        container.reload()

        status = container.status.lower()

        if status == "running":
            return False, "Container is already running."

        if status not in ("exited", "created", "dead"):
            return False, f"Cannot restart container in '{status}' state."

        container.restart(timeout=10)
        container.reload()

        if container.status.lower() == "running":
            return True, (
                f"Container '{container.name}' restarted successfully."
            )

        return False, (
            "Restart command completed, but container is not running."
        )

    except docker.errors.NotFound:
        return False, "Container not found."

    except docker.errors.DockerException as e:
        print(f"Container restart error: {e}")
        return False, (
            "Docker error occurred while restarting the container."
        )

    except Exception as e:
        print(f"Unexpected restart error: {e}")
        return False, "An unexpected error occurred."
