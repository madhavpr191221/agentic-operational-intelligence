"""Small SQLite append-only event and investigation store."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class EventStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("AOI_DB_PATH", ".data/agentic_ops.sqlite3"))
        self._schema_lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._schema_lock, self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    arrival_model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evidence_id TEXT UNIQUE NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    simulated_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_events_run_source ON events(run_id, source, event_type);
                CREATE TABLE IF NOT EXISTS truths (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    truth_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investigations (
                    investigation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    report_json TEXT,
                    error TEXT
                );
            """)

    def create_run(self, scenario: str, seed: int, arrival_model: str, config: dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (run_id, utc_now(), scenario, seed, arrival_model, "queued", json.dumps(config)),
            )
        return run_id

    def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        with self._connection() as connection:
            connection.execute("UPDATE runs SET status=?, error=? WHERE run_id=?", (status, error, run_id))

    def add_truth(self, run_id: str, truth: dict[str, Any]) -> None:
        """Evaluator-only data. No evidence tool reads this table."""
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO truths VALUES (?, ?)", (run_id, json.dumps(truth)))

    def get_truth(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT truth_json FROM truths WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def add_event(self, run_id: str, simulated_at: str, source: str, event_type: str,
                  payload: dict[str, Any]) -> dict[str, Any]:
        evidence_id = str(uuid.uuid4())
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO events(evidence_id,run_id,simulated_at,source,event_type,payload_json) VALUES(?,?,?,?,?,?)",
                (evidence_id, run_id, simulated_at, source, event_type, json.dumps(payload)),
            )
            sequence = cursor.lastrowid
        return {"sequence": sequence, "evidence_id": evidence_id, "run_id": run_id,
                "simulated_at": simulated_at, "source": source, "event_type": event_type, "payload": payload}

    def list_events(self, run_id: str, after_sequence: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (run_id, after_sequence, min(max(limit, 1), 2000)),
            ).fetchall()
        return [self._event(row) for row in rows]

    def search_events(self, run_id: str, sources: tuple[str, ...], query: str = "", limit: int = 30,
                      event_types: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        if not sources:
            return []
        source_slots = ",".join("?" for _ in sources)
        sql = f"SELECT * FROM events WHERE run_id=? AND source IN ({source_slots})"
        params: list[Any] = [run_id, *sources]
        if event_types:
            type_slots = ",".join("?" for _ in event_types)
            sql += f" AND event_type IN ({type_slots})"
            params.extend(event_types)
        sql += " ORDER BY sequence DESC LIMIT ?"
        params.append(min(max(limit * 8, 1), 1000))
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        records = [self._event(row) for row in rows]
        if query.strip():
            terms = [term.casefold() for term in query.split() if len(term) > 1]
            records = [record for record in records if all(term in json.dumps(record["payload"]).casefold()
                                                            for term in terms)]
        return list(reversed(records[-limit:]))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {"run_id": row["run_id"], "created_at": row["created_at"], "scenario": row["scenario"],
                "seed": row["seed"], "arrival_model": row["arrival_model"], "status": row["status"],
                "config": json.loads(row["config_json"]), "error": row["error"]}

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as connection:
            ids = connection.execute("SELECT run_id FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [run for row in ids if (run := self.get_run(row[0])) is not None]

    def create_investigation(self, run_id: str, question: str) -> str:
        investigation_id = str(uuid.uuid4())
        with self._connection() as connection:
            connection.execute("INSERT INTO investigations VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                               (investigation_id, run_id, question, "queued", utc_now()))
        return investigation_id

    def set_investigation(self, investigation_id: str, status: str,
                          report: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._connection() as connection:
            connection.execute("UPDATE investigations SET status=?, report_json=?, error=? WHERE investigation_id=?",
                               (status, json.dumps(report) if report is not None else None, error, investigation_id))

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM investigations WHERE investigation_id=?", (investigation_id,)).fetchone()
        if not row:
            return None
        return {"investigation_id": row["investigation_id"], "run_id": row["run_id"],
                "question": row["question"], "status": row["status"], "created_at": row["created_at"],
                "report": json.loads(row["report_json"]) if row["report_json"] else None, "error": row["error"]}

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, Any]:
        return {"sequence": row["sequence"], "evidence_id": row["evidence_id"], "run_id": row["run_id"],
                "simulated_at": row["simulated_at"], "source": row["source"], "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"])}
