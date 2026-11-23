
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify
from threading import Thread, Event

from services.docker_service import (
    get_containers,
    restart_container,
    get_docker_client
)
from services.health_service import analyze_containers
from services.recovery_service import check_and_recover_containers
from services.metrics_db import (
    initialize_database,
    save_metrics,
    get_recent_metrics,
    get_auto_recovery_configs,
    set_auto_recovery,
    get_recovery_logs,
)


app = Flask(__name__)
app.secret_key = "containerhealth-local-dev-key"

SAMPLE_INTERVAL = 15
collector_stop = Event()


def check_docker_connection():
    """Return True only when the Docker Engine is reachable."""

    client = None

    try:
        client = get_docker_client()

        if client is None:
            return False

        client.ping()
        return True

    except Exception as e:
        print(f"Docker connection check failed: {e}")
        return False

    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def metrics_collector():
    """Collect metrics and check automatic recovery periodically."""

    while not collector_stop.is_set():

        try:
            containers = get_containers()
            save_metrics(containers)
        except Exception as e:
            print(f"Metrics collection error: {e}")

        try:
            events = check_and_recover_containers()

            if events:
                print(f"Automatic recovery events: {len(events)}")

        except Exception as e:
            print(f"Automatic recovery error: {e}")

        collector_stop.wait(SAMPLE_INTERVAL)


@app.route("/")
def home():
    containers = get_containers()

    return render_template(
        "index.html",
        containers=containers,
        active_page="dashboard"
    )


@app.route("/containers")
def containers_page():
    containers = get_containers()

    return render_template(
        "containers.html",
        containers=containers,
        active_page="containers"
    )


@app.route("/monitoring")
def monitoring():
    containers = get_containers()

    try:
        save_metrics(containers)
    except Exception as e:
        print(f"Could not save metrics snapshot: {e}")

    requested_id = request.args.get("container_id")

    selected_container = next(
        (
            container for container in containers
            if container["id"] == requested_id
        ),
        None
    )

    if selected_container is None and containers:
        selected_container = containers[0]

    selected_container_id = (
        selected_container["id"] if selected_container else None
    )

    history = (
        get_recent_metrics(
            container_id=selected_container_id,
            limit=60
        )
        if selected_container_id
        else []
    )

    return render_template(
        "monitoring.html",
        containers=containers,
        history=history,
        selected_container=selected_container,
        active_page="monitoring"
    )


@app.route("/health")
def health():
    containers = get_containers()
    analyzed_containers = analyze_containers(containers)

    critical_count = sum(
        1 for container in analyzed_containers
        if container["risk_level"] == "Critical"
    )

    warning_count = sum(
        1 for container in analyzed_containers
        if container["risk_level"] == "Warning"
    )

    healthy_count = sum(
        1 for container in analyzed_containers
        if container["risk_level"] == "Healthy"
    )

    return render_template(
        "health.html",
        containers=analyzed_containers,
        critical_count=critical_count,
        warning_count=warning_count,
        healthy_count=healthy_count,
        active_page="health"
    )


@app.route("/recovery")
def recovery():
    containers = get_containers()
    configs = get_auto_recovery_configs()

    recovery_configs = {
        config["container_id"]: config
        for config in configs
    }

    return render_template(
        "coming_soon.html",
        active_page="recovery",
        module="Container Recovery System",
        phase="PHASE 4",
        description=(
            "Monitor container availability, manage automatic "
            "recovery, and manually restart stopped containers."
        ),
        containers=containers,
        recovery_configs=recovery_configs,
        docker_connected=check_docker_connection()
    )


@app.route("/api/docker-status")
def docker_status():
    """Provide the current Docker Engine connection status."""

    return jsonify({
        "connected": check_docker_connection()
    })


@app.route("/recovery/history")
def recovery_history():
    """Display the latest recovery activity."""

    logs = get_recovery_logs(limit=100)

    return render_template(
        "recovery_history.html",
        logs=logs,
        active_page="recovery"
    )


@app.route("/recovery/auto/<container_id>", methods=["POST"])
def configure_auto_recovery(container_id):
    containers = get_containers()

    selected_container = next(
        (
            container for container in containers
            if container["id"] == container_id
        ),
        None
    )

    if selected_container is None:
        flash(
            "Container not found. Configuration was not changed.",
            "error"
        )
        return redirect(url_for("recovery"))

    enabled = request.form.get("enabled") == "true"

    try:
        set_auto_recovery(
            container_id=selected_container["id"],
            container_name=selected_container["name"],
            enabled=enabled,
            max_attempts=3,
            cooldown_seconds=60
        )

        if enabled:
            flash(
                f"Automatic recovery enabled for "
                f"{selected_container['name']}.",
                "success"
            )
        else:
            flash(
                f"Automatic recovery disabled for "
                f"{selected_container['name']}.",
                "success"
            )

    except Exception as e:
        flash(f"Could not update recovery settings: {e}", "error")

    return redirect(url_for("recovery"))


@app.route("/recovery/restart/<container_id>", methods=["POST"])
def recovery_restart(container_id):
    success, message = restart_container(container_id)

    if success:
        flash(message, "success")
    else:
        flash(message, "error")

    return redirect(url_for("recovery"))


if __name__ == "__main__":
    initialize_database()

    collector = Thread(
        target=metrics_collector,
        daemon=True
    )
    collector.start()

    app.run(
        debug=True,
        use_reloader=False,
        host="0.0.0.0",
        port=5000
    )
