"""Durable, count-only detector host with a transactional episode outbox.

The coordinator applies episode events and creates investigations durably.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import asdict
from typing import Any

from agentic_ops.detection import AlertEpisodeTracker, CategoryDetectorSuite, CountObservation
from agentic_ops.store import EventStore, utc_now


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(*parts: object) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, _json(parts)))


class DetectorHost:
    """Process ordered complete observations atomically for one isolated stream."""

    def __init__(self, store: EventStore, stream_id: str, suite: CategoryDetectorSuite):
        if not stream_id:
            raise ValueError("stream_id is required")
        self.store = store
        self.stream_id = stream_id
        self.suite = suite
        self.tracker = AlertEpisodeTracker()
        self._initial_state = suite.checkpoint()
        config = {category: asdict(suite.suites[category].config) for category in suite.categories}
        self.config_hash = hashlib.sha256(_json(config).encode()).hexdigest()
        with self.store._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS detector_host_checkpoints (
                    stream_id TEXT PRIMARY KEY, config_hash TEXT NOT NULL,
                    window_index INTEGER NOT NULL, window_end TEXT NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS detector_host_observations (
                    stream_id TEXT NOT NULL, window_index INTEGER NOT NULL,
                    observation_json TEXT NOT NULL, result_json TEXT NOT NULL,
                    PRIMARY KEY (stream_id, window_index)
                );
                CREATE TABLE IF NOT EXISTS episode_outbox (
                    event_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    window_index INTEGER NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, delivered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_episode_outbox_pending
                    ON episode_outbox(stream_id, delivered_at, window_index);
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL,
                    category TEXT NOT NULL, status TEXT NOT NULL,
                    opened_at TEXT NOT NULL, latest_window_end TEXT NOT NULL,
                    last_event_id TEXT NOT NULL, episode_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incident_jobs (
                    job_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL, reason TEXT NOT NULL,
                    cutoff TEXT NOT NULL, sequence_bound INTEGER NOT NULL,
                    episode_json TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, report_json TEXT, error TEXT,
                    FOREIGN KEY (incident_id) REFERENCES incidents(incident_id)
                );
                CREATE INDEX IF NOT EXISTS idx_incident_jobs_status ON incident_jobs(status, created_at);
            """)
            # Serialize the additive migration across hosts and retain the old
            # status column as the statistical episode state for API clients.
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(incidents)")}
            if "operational_status" not in columns:
                db.execute("ALTER TABLE incidents ADD COLUMN operational_status TEXT NOT NULL DEFAULT 'open'")
            db.execute("CREATE TABLE IF NOT EXISTS applied_episode_events ("
                       "event_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, applied_at TEXT NOT NULL)")
            # Older hosts already committed these deliveries with their effects.
            db.execute("INSERT OR IGNORE INTO applied_episode_events (event_id,stream_id,applied_at) "
                       "SELECT event_id,stream_id,delivered_at FROM episode_outbox WHERE delivered_at IS NOT NULL")

    def process(self, observation: CountObservation) -> dict[str, Any]:
        """Persist an observation, next checkpoint and its events in one transaction."""
        if set(observation.category_counts) != set(self.suite.categories):
            raise ValueError("A complete count for every configured category is required")
        body = {"window_index": observation.window_index, "window_start": observation.window_start,
                "window_end": observation.window_end, "category_counts": dict(observation.category_counts)}
        if observation.window_start >= observation.window_end:
            raise ValueError("Window start must precede window end")
        encoded = _json(body)
        # SQLite serializes competing hosts before either loads its checkpoint.
        db = self.store._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT observation_json,result_json FROM detector_host_observations "
                "WHERE stream_id=? AND window_index=?", (self.stream_id, observation.window_index)
            ).fetchone()
            if previous:
                if previous["observation_json"] != encoded:
                    raise ValueError("Observation already exists with different counts or bounds")
                db.commit()
                return json.loads(previous["result_json"])
            checkpoint = db.execute("SELECT * FROM detector_host_checkpoints WHERE stream_id=?",
                                    (self.stream_id,)).fetchone()
            if checkpoint:
                if checkpoint["config_hash"] != self.config_hash:
                    raise ValueError("Detector configuration differs from saved checkpoint")
                if observation.window_index != checkpoint["window_index"] + 1 or observation.window_start != checkpoint["window_end"]:
                    raise ValueError("Missing or out-of-order window; detection is paused")
                state = json.loads(checkpoint["state_json"])
                self.suite.restore(state["detector"])
                self.tracker.restore(state["episodes"])
            elif observation.window_index != 0:
                raise ValueError("A new stream must begin with window zero")
            else:
                self.suite.restore(self._initial_state)
                self.tracker.restore({})
            result = self.suite.update(observation).to_dict()
            for category, detection in result["categories"].items():
                before = self.tracker.checkpoint().get(category)
                episode = self.tracker.update(category, observation.window_index,
                                              detection["observed"], detection["alerted"])
                event_type = ("episode_opened" if episode and not before else
                              "episode_updated" if episode else "episode_ended" if before else None)
                if event_type is None:
                    continue
                active = episode or before
                episode_id = _id("episode", self.stream_id, category, active["start_window"])
                event_id = _id("episode-event", episode_id, event_type, observation.window_index, 1)
                payload = {"schema_version": 1, "event_id": event_id, "episode_id": episode_id,
                           "stream_id": self.stream_id, "event_type": event_type, "category": category,
                           "window": body, "detection": detection, "episode": active}
                db.execute("INSERT INTO episode_outbox VALUES (?,?,?,?,?,?,?,NULL)",
                           (event_id, self.stream_id, episode_id, event_type, observation.window_index,
                            _json(payload), utc_now()))
            state = {"detector": self.suite.checkpoint(), "episodes": self.tracker.checkpoint()}
            db.execute("INSERT INTO detector_host_observations VALUES (?,?,?,?)",
                       (self.stream_id, observation.window_index, encoded, _json(result)))
            db.execute("INSERT INTO detector_host_checkpoints VALUES (?,?,?,?,?) "
                       "ON CONFLICT(stream_id) DO UPDATE SET config_hash=excluded.config_hash, "
                       "window_index=excluded.window_index, window_end=excluded.window_end, state_json=excluded.state_json",
                       (self.stream_id, self.config_hash, observation.window_index, observation.window_end, _json(state)))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def monitoring_snapshot(self) -> dict[str, Any]:
        """Return the count-only baseline and display limits for the monitor."""
        return self.suite.monitoring_snapshot()

    def pending_events(self) -> list[dict[str, Any]]:
        """Read undelivered events in window and insertion order."""
        with self.store._connection() as db:
            rows = db.execute("SELECT payload_json FROM episode_outbox WHERE stream_id=? AND delivered_at IS NULL "
                              "ORDER BY window_index, rowid", (self.stream_id,)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def deliver_pending(self, automatic: bool | None = None) -> int:
        """Apply events and queue automatic investigations in the same transaction."""
        if automatic is None:
            automatic = os.getenv("AOI_INVESTIGATION_MODE", "automatic").lower() == "automatic"
        db = self.store._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM episode_outbox WHERE stream_id=? AND delivered_at IS NULL "
                              "ORDER BY window_index, rowid", (self.stream_id,)).fetchall()
            for row in rows:
                event = json.loads(row["payload_json"])
                window = event["window"]
                applied = db.execute("SELECT stream_id FROM applied_episode_events WHERE event_id=?",
                                     (event["event_id"],)).fetchone()
                if applied:
                    if applied["stream_id"] != self.stream_id:
                        raise ValueError("Episode event belongs to another stream")
                    db.execute("UPDATE episode_outbox SET delivered_at=? WHERE event_id=?",
                               (utc_now(), event["event_id"]))
                    continue
                if event["event_type"] == "episode_opened":
                    db.execute("INSERT OR IGNORE INTO incidents "
                               "(incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                               "VALUES (?,?,?,?,?,?,?,?)",
                               (event["episode_id"], self.stream_id, event["category"], "active",
                                window["window_start"], window["window_end"], event["event_id"],
                                _json(event["episode"])))
                    if automatic:
                        # The anomaly evidence is written by the replay before
                        # delivery. Queue creation and outbox acknowledgement
                        # share this transaction, so a restart cannot lose the
                        # initial investigation between those two operations.
                        alert = db.execute(
                            "SELECT sequence,payload_json FROM events WHERE run_id=? "
                            "AND event_type='anomaly_alert' "
                            "AND json_extract(payload_json,'$.category')=? "
                            "AND json_extract(payload_json,'$.window_index')=? "
                            "ORDER BY sequence DESC LIMIT 1",
                            (self.stream_id, event["category"], window["window_index"]),
                        ).fetchone()
                        if alert is None:
                            raise ValueError("Cannot deliver an opened episode before its alert evidence is stored")
                        job_id = _id("incident-job", event["episode_id"], event["event_id"])
                        db.execute(
                            "INSERT OR IGNORE INTO incident_jobs "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (job_id, event["episode_id"], self.stream_id, "initial alert",
                             json.loads(alert["payload_json"])["window_end"], alert["sequence"],
                             _json(event["episode"]), "queued", utc_now(), None, None),
                        )
                else:
                    updated = db.execute("UPDATE incidents SET status=?,latest_window_end=?,last_event_id=?,episode_json=? "
                                         "WHERE incident_id=? AND stream_id=? AND category=?",
                                         ("ended" if event["event_type"] == "episode_ended" else "active",
                                          window["window_start"] if event["event_type"] == "episode_ended" else window["window_end"],
                                          event["event_id"], _json(event["episode"]),
                                          event["episode_id"], self.stream_id, event["category"]))
                    if updated.rowcount != 1:
                        raise ValueError("Cannot update an unknown episode")
                    if automatic and event["event_type"] == "episode_updated":
                        self._queue_material_followup(db, event)
                db.execute("INSERT INTO applied_episode_events (event_id,stream_id,applied_at) VALUES (?,?,?)",
                           (event["event_id"], self.stream_id, utc_now()))
                db.execute("UPDATE episode_outbox SET delivered_at=? WHERE event_id=? AND delivered_at IS NULL",
                           (utc_now(), event["event_id"]))
            db.commit()
            return len(rows)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _queue_material_followup(self, db: sqlite3.Connection, event: dict[str, Any]) -> None:
        """Queue at most one follow-up per window when linked evidence changes materially."""
        category = event["category"]
        service = {"payments": "checkout-payments", "orders": "orders-api",
                   "delivery": "delivery-api"}.get(category)
        if service is None:
            return
        window = event["window"]
        start, end = window["window_start"], window["window_end"]
        episode_id = event["episode_id"]
        latest_job = db.execute(
            "SELECT * FROM incident_jobs WHERE incident_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        if latest_job and latest_job["cutoff"] >= end:
            return
        if latest_job and latest_job["status"] in {"queued", "running"}:
            return

        current = db.execute(
            "SELECT source,payload_json FROM events WHERE run_id=? AND simulated_at>=? AND simulated_at<? "
            "AND source IN ('product_logs','issue_tracker','change_log') ORDER BY sequence",
            (self.stream_id, start, end),
        ).fetchall()
        relevant: list[dict[str, Any]] = []
        current_logs: list[dict[str, Any]] = []
        for row in current:
            payload = json.loads(row["payload_json"])
            if payload.get("service") != service:
                continue
            if row["source"] == "product_logs":
                current_logs.append(payload)
            else:
                relevant.append(payload)

        changed = bool(relevant)
        for payload in current_logs:
            requests = int(payload.get("requests") or 0)
            errors = int(payload.get("errors") or 0)
            error_rate = errors / requests if requests else 0.0
            if service == "checkout-payments":
                degraded = error_rate >= 0.10
            elif service == "orders-api":
                degraded = int(payload.get("queue_depth") or 0) > 100 or error_rate >= 0.10
            else:
                degraded = int(payload.get("p95_latency_ms") or 0) >= 1000 or error_rate >= 0.10
            previous = db.execute(
                "SELECT payload_json FROM events WHERE run_id=? AND source='product_logs' "
                "AND event_type='service_log_summary' AND simulated_at<? "
                "AND json_extract(payload_json,'$.service')=? ORDER BY simulated_at DESC,sequence DESC LIMIT 1",
                (self.stream_id, start, service),
            ).fetchone()
            if previous:
                old = json.loads(previous["payload_json"])
                old_requests = int(old.get("requests") or 0)
                old_rate = int(old.get("errors") or 0) / old_requests if old_requests else 0.0
                if service == "checkout-payments":
                    was_degraded = old_rate >= 0.10
                elif service == "orders-api":
                    was_degraded = int(old.get("queue_depth") or 0) > 100 or old_rate >= 0.10
                else:
                    was_degraded = int(old.get("p95_latency_ms") or 0) >= 1000 or old_rate >= 0.10
                changed = changed or (was_degraded != degraded)
            elif degraded:
                changed = True
        if not changed:
            return
        reason = "Relevant service or issue update"
        self._insert_coordinator_job(db, event, reason, end)

    def _insert_coordinator_job(self, db: sqlite3.Connection, event: dict[str, Any],
                                reason: str, cutoff: str) -> None:
        incident_id = event["episode_id"]
        sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?",
                              (self.stream_id,)).fetchone()[0]
        job_id = _id("incident-job", incident_id, event["event_id"], reason)
        db.execute("INSERT OR IGNORE INTO incident_jobs "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, incident_id, self.stream_id, reason, cutoff, sequence,
                    _json(event["episode"]), "queued", utc_now(), None, None))


def list_incidents(store: EventStore, stream_id: str | None = None) -> list[dict[str, Any]]:
    """List incident registry entries created by the durable coordinator."""
    with store._connection() as db:
        query = "SELECT * FROM incidents"
        params: tuple = ()
        if stream_id is not None:
            query += " WHERE stream_id=?"
            params = (stream_id,)
        rows = db.execute(query + " ORDER BY opened_at DESC,incident_id", params).fetchall()
    return [{**dict(row), "episode": json.loads(row["episode_json"])} for row in rows]
