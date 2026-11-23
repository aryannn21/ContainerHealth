
from datetime import datetime, timezone, timedelta

from services.docker_service import get_docker_client
from services.metrics_db import (
    get_auto_recovery_configs,
    get_recovery_state,
    record_auto_recovery_attempt,
    reset_recovery_attempts,
)


STABLE_RUNNING_SECONDS = 120


def is_cooldown_active(last_attempt, cooldown_seconds):
    if not last_attempt:
        return False

    try:
        previous_attempt = datetime.fromisoformat(last_attempt)

        if previous_attempt.tzinfo is None:
            previous_attempt = previous_attempt.replace(
                tzinfo=timezone.utc
            )

        cooldown_end = previous_attempt + timedelta(
            seconds=cooldown_seconds
        )

        return datetime.now(timezone.utc) < cooldown_end

    except (ValueError, TypeError):
        return False


def check_and_recover_containers():
    """Recover enrolled containers after abnormal exits."""

    recovery_events = []
    configs = get_auto_recovery_configs()

    if not configs:
        return recovery_events

    client = get_docker_client()

    if client is None:
        print("Automatic recovery: Docker Engine unavailable.")
        return recovery_events

    try:
        for config in configs:

            container_id = config["container_id"]
            container_name = config["container_name"]

            if not config["enabled"]:
                continue

            try:
                container = client.containers.get(container_id)
                container.reload()

                state = container.attrs.get("State", {})
                status = state.get("Status", container.status).lower()

                recovery_state = get_recovery_state(container_id)

                # Reset retry history only after sustained running time.
                if status == "running":
                    started_at = state.get("StartedAt")

                    try:
                        started = datetime.fromisoformat(
                            started_at.replace("Z", "+00:00")
                        )

                        running_seconds = (
                            datetime.now(timezone.utc) - started
                        ).total_seconds()

                        if (
                            recovery_state["attempts"] > 0
                            and running_seconds >= STABLE_RUNNING_SECONDS
                        ):
                            reset_recovery_attempts(container_id)

                    except (ValueError, TypeError, AttributeError):
                        pass

                    continue

                if status not in ("exited", "dead"):
                    continue

                exit_code = state.get("ExitCode")
                oom_killed = state.get("OOMKilled", False)

                abnormal_exit = (
                    oom_killed
                    or (
                        exit_code is not None
                        and exit_code != 0
                    )
                )

                if not abnormal_exit:
                    continue

                max_attempts = int(config["max_attempts"])
                cooldown_seconds = int(config["cooldown_seconds"])

                if recovery_state["attempts"] >= max_attempts:
                    continue

                if is_cooldown_active(
                    recovery_state["last_attempt"],
                    cooldown_seconds
                ):
                    continue

                try:
                    container.restart(timeout=10)
                    container.reload()

                    if container.status.lower() == "running":
                        result = "Success"
                        message = (
                            f"Container '{container_name}' "
                            "was automatically restarted."
                        )
                    else:
                        result = "Failed"
                        message = (
                            f"Restart attempted, but '{container_name}' "
                            "is not running."
                        )

                except Exception as error:
                    result = "Failed"
                    message = (
                        f"Restart failed for '{container_name}': {error}"
                    )

                record_auto_recovery_attempt(
                    container_id=container_id,
                    container_name=container_name,
                    result=result,
                    message=message
                )

                recovery_events.append({
                    "container_id": container_id,
                    "container_name": container_name,
                    "result": result,
                    "message": message
                })

                print(message)

            except Exception as error:
                message = (
                    f"Could not process '{container_name}': {error}"
                )
                print(message)

                recovery_events.append({
                    "container_id": container_id,
                    "container_name": container_name,
                    "result": "Error",
                    "message": message
                })

    finally:
        client.close()

    return recovery_events
