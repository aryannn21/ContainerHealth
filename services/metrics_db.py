
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


# Store the database in the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "containerhealth.db"


def get_connection():
    connection = sqlite3.connect(str(DB_PATH), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def current_timestamp():
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def initialize_database():
    """Create all database tables and indexes if they do not exist."""

    with get_connection() as connection:

        # Existing metrics table.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS metric_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                container_id TEXT NOT NULL,
                container_name TEXT NOT NULL,
                status TEXT NOT NULL,
                cpu_percent REAL,
                memory_usage_mb REAL,
                memory_limit_mb REAL,
                memory_percent REAL,
                network_rx_mb REAL,
                network_tx_mb REAL
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_container_time
            ON metric_snapshots (container_id, timestamp)
        """)

        # Automatic recovery configuration.
        # Containers must be explicitly enabled.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS auto_recovery_config (
                container_id TEXT PRIMARY KEY,
                container_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Tracks attempts for each container.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS auto_recovery_state (
                container_id TEXT PRIMARY KEY,
                container_name TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt TEXT,
                last_status TEXT,
                updated_at TEXT NOT NULL
            )
        """)

        # Recovery action history.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS recovery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                container_id TEXT NOT NULL,
                container_name TEXT NOT NULL,
                recovery_type TEXT NOT NULL,
                action TEXT NOT NULL,
                result TEXT NOT NULL,
                message TEXT
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_recovery_logs_container
            ON recovery_logs (container_id, timestamp)
        """)


# --------------------------------------------------
# METRICS FUNCTIONS
# --------------------------------------------------

def save_metrics(containers):
    """Save one timestamped snapshot for each discovered container."""

    timestamp = current_timestamp()
    rows = []

    for container in containers:
        rows.append((
            timestamp,
            container.get("id", ""),
            container.get("name", "Unknown"),
            container.get("status", "unknown"),
            container.get("cpu_percent"),
            container.get("memory_usage_mb"),
            container.get("memory_limit_mb"),
            container.get("memory_percent"),
            container.get("network_rx_mb"),
            container.get("network_tx_mb"),
        ))

    if not rows:
        return

    with get_connection() as connection:
        connection.executemany("""
            INSERT INTO metric_snapshots (
                timestamp,
                container_id,
                container_name,
                status,
                cpu_percent,
                memory_usage_mb,
                memory_limit_mb,
                memory_percent,
                network_rx_mb,
                network_tx_mb
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)


def get_recent_metrics(container_id=None, limit=60):
    """Return the newest metric snapshots, oldest first for charting."""

    limit = max(1, min(int(limit), 1000))

    with get_connection() as connection:

        if container_id:
            rows = connection.execute("""
                SELECT *
                FROM metric_snapshots
                WHERE container_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (container_id, limit)).fetchall()

        else:
            rows = connection.execute("""
                SELECT *
                FROM metric_snapshots
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()

    return [dict(row) for row in reversed(rows)]


# --------------------------------------------------
# AUTOMATIC RECOVERY CONFIGURATION
# --------------------------------------------------

def set_auto_recovery(
    container_id,
    container_name,
    enabled=True,
    max_attempts=3,
    cooldown_seconds=60
):
    """Enable or disable automatic recovery for a container."""

    if not container_id or not container_name:
        raise ValueError("Container ID and name are required.")

    max_attempts = int(max_attempts)
    cooldown_seconds = int(cooldown_seconds)

    if max_attempts < 1:
        raise ValueError("Maximum attempts must be at least 1.")

    if cooldown_seconds < 1:
        raise ValueError("Cooldown must be at least 1 second.")

    timestamp = current_timestamp()

    with get_connection() as connection:
        connection.execute("""
            INSERT INTO auto_recovery_config (
                container_id,
                container_name,
                enabled,
                max_attempts,
                cooldown_seconds,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(container_id)
            DO UPDATE SET
                container_name = excluded.container_name,
                enabled = excluded.enabled,
                max_attempts = excluded.max_attempts,
                cooldown_seconds = excluded.cooldown_seconds,
                updated_at = excluded.updated_at
        """, (
            container_id,
            container_name,
            int(bool(enabled)),
            max_attempts,
            cooldown_seconds,
            timestamp,
            timestamp
        ))

    return True


def get_auto_recovery_configs():
    """Return automatic recovery settings for all configured containers."""

    with get_connection() as connection:
        rows = connection.execute("""
            SELECT *
            FROM auto_recovery_config
            ORDER BY container_name
        """).fetchall()

    return [dict(row) for row in rows]


def get_auto_recovery_config(container_id):
    """Return automatic recovery settings for one container."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM auto_recovery_config
            WHERE container_id = ?
        """, (container_id,)).fetchone()

    return dict(row) if row else None


# --------------------------------------------------
# AUTOMATIC RECOVERY ATTEMPT TRACKING
# --------------------------------------------------

def get_recovery_state(container_id):
    """Return the current recovery attempt state for a container."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM auto_recovery_state
            WHERE container_id = ?
        """, (container_id,)).fetchone()

    return dict(row) if row else {
        "container_id": container_id,
        "container_name": None,
        "attempts": 0,
        "last_attempt": None,
        "last_status": None,
        "updated_at": None
    }


def record_auto_recovery_attempt(
    container_id,
    container_name,
    result,
    message
):
    """Record an automatic recovery attempt and update its counter."""

    timestamp = current_timestamp()

    with get_connection() as connection:

        connection.execute("""
            INSERT INTO auto_recovery_state (
                container_id,
                container_name,
                attempts,
                last_attempt,
                last_status,
                updated_at
            )
            VALUES (?, ?, 1, ?, ?, ?)

            ON CONFLICT(container_id)
            DO UPDATE SET
                container_name = excluded.container_name,
                attempts = auto_recovery_state.attempts + 1,
                last_attempt = excluded.last_attempt,
                last_status = excluded.last_status,
                updated_at = excluded.updated_at
        """, (
            container_id,
            container_name,
            timestamp,
            result,
            timestamp
        ))

        connection.execute("""
            INSERT INTO recovery_logs (
                timestamp,
                container_id,
                container_name,
                recovery_type,
                action,
                result,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            container_id,
            container_name,
            "Automatic",
            "Restart",
            result,
            message
        ))


def reset_recovery_attempts(container_id):
    """Reset automatic recovery attempts after a container recovers."""

    timestamp = current_timestamp()

    with get_connection() as connection:
        connection.execute("""
            UPDATE auto_recovery_state
            SET attempts = 0,
                last_attempt = NULL,
                last_status = 'Recovered',
                updated_at = ?
            WHERE container_id = ?
        """, (timestamp, container_id))


# --------------------------------------------------
# RECOVERY LOGGING
# --------------------------------------------------

def record_recovery_log(
    container_id,
    container_name,
    recovery_type,
    action,
    result,
    message=""
):
    """Record a manual or automatic recovery action."""

    timestamp = current_timestamp()

    with get_connection() as connection:
        connection.execute("""
            INSERT INTO recovery_logs (
                timestamp,
                container_id,
                container_name,
                recovery_type,
                action,
                result,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            container_id,
            container_name,
            recovery_type,
            action,
            result,
            message
        ))


def get_recovery_logs(limit=100):
    """Return recent recovery history, newest first."""

    limit = max(1, min(int(limit), 1000))

    with get_connection() as connection:
        rows = connection.execute("""
            SELECT *
            FROM recovery_logs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    return [dict(row) for row in rows]
