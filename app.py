"""Local FastAPI server for the Raspberry Pi activity monitor."""

import json
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "events.db"
INDEX_PATH = BASE_DIR / "static" / "index.html"

AMBER_THRESHOLD = timedelta(minutes=2)
RED_THRESHOLD = timedelta(minutes=3)
# Heartbeats in the screenshot arrive about every 10 seconds. If none arrives
# for 30 seconds, consider the continuous "switch on" sequence finished.
HEARTBEAT_MISSING_THRESHOLD = timedelta(seconds=30)

# The ESP32 sends repeated heartbeats while the switch is on.
ON_EVENT_TYPES = {"heartbeat", "power_on", "switch_on", "on"}
OFF_EVENT_TYPES = {"power_off", "switch_off", "off"}


@contextmanager
def get_database() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection whose rows can be accessed by column name."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def create_tables() -> None:
    """Create the local event table and indexes when they do not exist."""
    with get_database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                received_at TEXT NOT NULL,
                raw_payload TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_events_node_received
            ON events (node_id, received_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_events_received
            ON events (received_at DESC)
            """
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_tables()
    yield


app = FastAPI(
    title="Elderly Activity Monitor",
    description="Local Raspberry Pi event monitoring API",
    lifespan=lifespan,
)


class DeviceEvent(BaseModel):
    """Accept both the old receiver fields and the new API field names."""

    node_id: Optional[str] = None
    device_id: Optional[str] = None
    event_type: str = "heartbeat"
    # power_on starts a new sequence at count 0; heartbeats then count upward.
    count: int = Field(default=1, ge=0)
    raw_payload: Optional[Any] = None
    raw_event: Optional[Any] = None

    def resolved_node_id(self) -> str:
        node_id = self.node_id or self.device_id
        if not node_id or not node_id.strip():
            raise ValueError("node_id or device_id is required")
        return node_id.strip()

    def resolved_raw_payload(self) -> Any:
        return self.raw_payload if self.raw_payload is not None else self.raw_event


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def serialize_raw_payload(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def deserialize_raw_payload(value: Optional[str]) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def event_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "node_id": row["node_id"],
        "device_id": row["node_id"],  # Backward-compatible response field.
        "event_type": row["event_type"],
        "count": row["count"],
        "received_at": row["received_at"],
        "raw_payload": deserialize_raw_payload(row["raw_payload"]),
        "raw_event": deserialize_raw_payload(row["raw_payload"]),
    }


def flag_colour(duration: timedelta) -> str:
    """Return the traffic-light colour for either monitored condition."""
    if duration > RED_THRESHOLD:
        return "red"
    if duration >= AMBER_THRESHOLD:
        return "amber"
    return "green"


def humanize_age(last_seen: datetime, now: datetime) -> str:
    total_seconds = max(0, int((now - last_seen).total_seconds()))
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m"
    return f"{total_seconds // 86400}d {(total_seconds % 86400) // 3600}h"


def save_event(data: DeviceEvent) -> dict:
    try:
        node_id = data.resolved_node_id()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now().astimezone()
    received_at = now.isoformat(timespec="seconds")
    raw_payload = serialize_raw_payload(data.resolved_raw_payload())

    with get_database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO events (node_id, event_type, count, received_at, raw_payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (node_id, data.event_type, data.count, received_at, raw_payload),
        )
        event_id = cursor.lastrowid

    return {
        "id": event_id,
        "node_id": node_id,
        "device_id": node_id,
        "event_type": data.event_type,
        "count": data.count,
        "received_at": received_at,
        "raw_payload": data.resolved_raw_payload(),
    }


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(INDEX_PATH)


@app.post("/heartbeat")
def receive_heartbeat(data: DeviceEvent):
    """Original endpoint retained for the existing LoRa receiver."""
    event = save_event(data)
    return {"message": "event received", **event}


@app.post("/event")
@app.post("/events")
def receive_event(data: DeviceEvent):
    event = save_event(data)
    return {"message": "event received", **event}


@app.get("/events")
def get_events(limit: int = Query(default=50, ge=1, le=500)):
    with get_database() as connection:
        rows = connection.execute(
            "SELECT * FROM events ORDER BY received_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    events = [event_row_to_dict(row) for row in rows]
    return {"count": len(events), "events": events}


@app.get("/events/latest")
def get_latest_events_per_node():
    with get_database() as connection:
        rows = connection.execute(
            """
            SELECT e.*
            FROM events e
            WHERE e.id = (
                SELECT e2.id
                FROM events e2
                WHERE e2.node_id = e.node_id
                ORDER BY e2.received_at DESC, e2.id DESC
                LIMIT 1
            )
            ORDER BY e.node_id
            """
        ).fetchall()

    events = [event_row_to_dict(row) for row in rows]
    return {"count": len(events), "events": events}


def build_node_summary() -> dict:
    now = datetime.now().astimezone()
    with get_database() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM events
            ORDER BY node_id, received_at, id
            """
        ).fetchall()

    events_by_node: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        events_by_node.setdefault(row["node_id"], []).append(row)

    nodes = []
    status_counts = {"green": 0, "amber": 0, "red": 0}

    for node_id, node_events in events_by_node.items():
        latest = node_events[-1]
        last_seen = parse_timestamp(latest["received_at"])
        # Count database rows, not the ESP32's cumulative heartbeat counter.
        event_count = len(node_events)

        # This marks the beginning of the current uninterrupted "switch on"
        # sequence. A power_off event, a heartbeat gap, or an ESP32 counter
        # restart begins a new sequence.
        current_on_started: Optional[datetime] = None
        last_heartbeat: Optional[datetime] = None
        last_off: Optional[datetime] = None
        previous_heartbeat_count: Optional[int] = None

        for event in node_events:
            event_time = parse_timestamp(event["received_at"])
            event_type = event["event_type"].lower()
            if event_type in OFF_EVENT_TYPES:
                current_on_started = None
                last_heartbeat = None
                last_off = event_time
                previous_heartbeat_count = None
            elif event_type in ON_EVENT_TYPES:
                heartbeat_count = event["count"]
                heartbeat_gap = (
                    event_time - last_heartbeat if last_heartbeat else None
                )
                counter_restarted = (
                    previous_heartbeat_count is not None
                    and heartbeat_count < previous_heartbeat_count
                )
                gap_started_new_sequence = (
                    heartbeat_gap is not None
                    and heartbeat_gap >= HEARTBEAT_MISSING_THRESHOLD
                )

                if (
                    current_on_started is None
                    or gap_started_new_sequence
                    or counter_restarted
                ):
                    current_on_started = event_time

                last_heartbeat = event_time
                previous_heartbeat_count = heartbeat_count

        heartbeat_age = (
            max(now - last_heartbeat, timedelta()) if last_heartbeat else None
        )

        if (
            current_on_started is not None
            and heartbeat_age < HEARTBEAT_MISSING_THRESHOLD
        ):
            # Heartbeats are arriving: flag if the switch stays on too long.
            flag_type = "continuous_heartbeat"
            flag_label = "Switch on"
            flag_duration = max(now - current_on_started, timedelta())
        else:
            # No recent heartbeat: flag if the switch stays inactive too long.
            flag_type = "no_heartbeat"
            flag_label = "No heartbeat"
            # Use the newest relevant event. In particular, power_off must
            # reset a previously red continuous-heartbeat flag back to green.
            reference_time = max(
                time
                for time in (last_heartbeat, last_off, last_seen)
                if time is not None
            )
            # Start inactivity timing when the expected heartbeat becomes
            # overdue, rather than from the final heartbeat itself.
            if last_heartbeat is not None and last_heartbeat >= reference_time:
                reference_time += HEARTBEAT_MISSING_THRESHOLD
            flag_duration = max(now - reference_time, timedelta())

        status = flag_colour(flag_duration)
        status_counts[status] += 1
        nodes.append(
            {
                "node_id": node_id,
                "status": status,
                "flag_type": flag_type,
                "flag_label": flag_label,
                "flag_duration": humanize_age(now - flag_duration, now),
                "last_event_type": latest["event_type"],
                "last_seen": latest["received_at"],
                "time_since_last_seen": humanize_age(last_seen, now),
                "event_count": event_count,
            }
        )

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "total_nodes": len(nodes),
        **status_counts,
        # Compatibility aliases for clients using the previous names.
        "online": status_counts["green"],
        "warning": status_counts["amber"],
        "offline": status_counts["red"],
        "nodes": nodes,
    }


@app.get("/nodes/status")
def get_node_status_summary():
    return build_node_summary()


@app.get("/status")
def get_status():
    """Keep the old route while returning the richer multi-node summary."""
    summary = build_node_summary()
    if not summary["nodes"]:
        return {
            **summary,
            "device_id": None,
            "last_seen": None,
            "last_event_type": None,
            "device_status": "red",
        }

    most_recent = max(summary["nodes"], key=lambda node: node["last_seen"])
    return {
        **summary,
        "device_id": most_recent["node_id"],
        "last_seen": most_recent["last_seen"],
        "last_event_type": most_recent["last_event_type"],
        "device_status": most_recent["status"],
    }


@app.get("/health")
def health_check():
    try:
        with get_database() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "ok"}
    except sqlite3.Error as exc:
        return {"status": "error", "database": str(exc)}
