
def analyze_container(container):
    """Calculate a rule-based health score for a Docker container."""

    score = 100
    reasons = []

    status = str(container.get("status", "unknown")).lower()
    cpu = container.get("cpu_percent")
    memory = container.get("memory_percent")

    try:
        restarts = int(container.get("restart_count", 0) or 0)
    except (TypeError, ValueError):
        restarts = 0

    if status != "running":
        score = 0
        reasons.append(f"Container is not running (status: {status}).")
    else:
        if cpu is not None:
            if cpu >= 90:
                score -= 35
                reasons.append(f"Critical CPU usage: {cpu:.1f}%.")
            elif cpu >= 75:
                score -= 20
                reasons.append(f"High CPU usage: {cpu:.1f}%.")
            elif cpu >= 60:
                score -= 10
                reasons.append(f"Elevated CPU usage: {cpu:.1f}%.")

        if memory is not None:
            if memory >= 90:
                score -= 35
                reasons.append(f"Critical memory usage: {memory:.1f}%.")
            elif memory >= 75:
                score -= 20
                reasons.append(f"High memory usage: {memory:.1f}%.")
            elif memory >= 60:
                score -= 10
                reasons.append(f"Elevated memory usage: {memory:.1f}%.")

        if restarts >= 5:
            score -= 15
            reasons.append(f"Frequent restarts detected: {restarts}.")
        elif restarts >= 3:
            score -= 8
            reasons.append(f"Multiple restarts detected: {restarts}.")

    score = max(0, min(100, score))

    if score >= 80:
        risk = "Healthy"
    elif score >= 50:
        risk = "Warning"
    else:
        risk = "Critical"

    if not reasons:
        reasons.append("No configured risk thresholds have been exceeded.")

    result = container.copy()
    result["health_score"] = score
    result["risk_level"] = risk
    result["risk_reasons"] = reasons

    return result


def analyze_containers(containers):
    """Analyze all discovered Docker containers."""
    return [analyze_container(container) for container in containers]
