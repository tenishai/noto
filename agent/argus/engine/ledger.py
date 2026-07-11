"""SQLite ledger: one row per step per target_ref, plus an append-only event log.

Stdlib sqlite3 (design decision), WAL mode. `target_ref` carries the scope
(`storage@<region>`, `bootstrap@<cluster>`, `wire@<app>`), which is what makes region
work shared across clusters and app work independent per app. The clock is injectable
so tests are deterministic (testing.md).
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provision_steps (
    id INTEGER PRIMARY KEY,
    target_ref TEXT NOT NULL,
    step_name TEXT NOT NULL,
    tooling TEXT NOT NULL,
    status TEXT NOT NULL,
    resource_ref TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (target_ref, step_name)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    target_ref TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class StepRow:
    target_ref: str
    step_name: str
    tooling: str
    status: str
    resource_ref: str | None
    error: str | None
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class EventRow:
    target_ref: str
    type: str
    payload: dict
    created_at: str


class Ledger:
    def __init__(self, path: Path, now_fn: Callable[[], str] = utc_now_iso) -> None:
        self.path = path
        self.now = now_fn
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def step_status(self, target_ref: str, step_name: str) -> StepRow | None:
        row = self._conn.execute(
            "SELECT * FROM provision_steps WHERE target_ref = ? AND step_name = ?",
            (target_ref, step_name),
        ).fetchone()
        if row is None:
            return None
        return StepRow(
            target_ref=row["target_ref"],
            step_name=row["step_name"],
            tooling=row["tooling"],
            status=row["status"],
            resource_ref=row["resource_ref"],
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def start_step(self, target_ref: str, step_name: str, tooling: str) -> None:
        self._conn.execute(
            """INSERT INTO provision_steps (target_ref, step_name, tooling, status, started_at)
               VALUES (?, ?, ?, 'running', ?)
               ON CONFLICT (target_ref, step_name) DO UPDATE SET
                 status='running', error=NULL, started_at=excluded.started_at,
                 finished_at=NULL""",
            (target_ref, step_name, tooling, self.now()),
        )
        self._conn.commit()

    def finish_step(
        self,
        target_ref: str,
        step_name: str,
        status: str,
        resource_ref: str | None = None,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            """UPDATE provision_steps
               SET status = ?, resource_ref = ?, error = ?, finished_at = ?
               WHERE target_ref = ? AND step_name = ?""",
            (status, resource_ref, error, self.now(), target_ref, step_name),
        )
        self._conn.commit()

    def record_event(self, target_ref: str, type: str, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO events (target_ref, type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (target_ref, type, json.dumps(payload, sort_keys=True), self.now()),
        )
        self._conn.commit()

    def events(self, target_ref: str) -> list[EventRow]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE target_ref = ? ORDER BY id", (target_ref,)
        ).fetchall()
        return [
            EventRow(
                target_ref=r["target_ref"],
                type=r["type"],
                payload=json.loads(r["payload_json"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
