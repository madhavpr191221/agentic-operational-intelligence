"""Operator records and incident lifecycle for the local MVP workspace."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from agentic_ops.store import EventStore, utc_now


def _stable_id(kind: str, incident_id: str, idempotency_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"aoi:{kind}:{incident_id}:{idempotency_key}"))


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone is required for the action time.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class IncidentWorkspace:
    """Persist append-only human timeline entries and operational state changes."""

    def __init__(self, store: EventStore, operator_name: str = "Local operator"):
        self.store = store
        self.operator_name = operator_name.strip() or "Local operator"
        with self.store._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS incident_timeline (
                    entry_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    recorded_by TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(incident_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_incident_timeline_incident_time
                    ON incident_timeline(incident_id, occurred_at, recorded_at);
            """)

    def _append(self, db: Any, incident_id: str, event_type: str, occurred_at: str,
                idempotency_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("An idempotency key is required.")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        existing = db.execute(
            "SELECT * FROM incident_timeline WHERE incident_id=? AND idempotency_key=?",
            (incident_id, idempotency_key),
        ).fetchone()
        if existing:
            if existing["event_type"] != event_type or existing["payload_json"] != encoded:
                raise ValueError("This idempotency key was already used for a different incident update.")
            return self._decode(existing)
        entry = {
            "entry_id": _stable_id(event_type, incident_id, idempotency_key),
            "incident_id": incident_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "recorded_at": utc_now(),
            "recorded_by": self.operator_name,
            "idempotency_key": idempotency_key,
            "payload_json": encoded,
        }
        db.execute(
            "INSERT INTO incident_timeline(entry_id,incident_id,event_type,occurred_at,recorded_at,"
            "recorded_by,idempotency_key,payload_json) VALUES(?,?,?,?,?,?,?,?)",
            tuple(entry[key] for key in ("entry_id", "incident_id", "event_type", "occurred_at",
                                         "recorded_at", "recorded_by", "idempotency_key", "payload_json")),
        )
        return self._decode(entry)

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result.pop("idempotency_key", None)
        return result

    def record_action(self, incident_id: str, *, description: str, occurred_at: datetime,
                      idempotency_key: str, performed_by: str | None = None,
                      reference: str | None = None) -> dict[str, Any]:
        at = _utc_iso(occurred_at)
        payload = {
            "action_description": description.strip(),
            "performed_by": performed_by.strip() if performed_by and performed_by.strip() else None,
            "reference": reference.strip() if reference and reference.strip() else None,
            "source_type": "operator_reported",
        }
        if not payload["action_description"]:
            raise ValueError("Describe the action before saving it.")
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            incident = db.execute("SELECT operational_status FROM incidents WHERE incident_id=?",
                                  (incident_id,)).fetchone()
            if incident is None:
                raise KeyError("Unknown incident")
            prior = db.execute("SELECT * FROM incident_timeline WHERE incident_id=? AND idempotency_key=?",
                               (incident_id, idempotency_key)).fetchone()
            if prior:
                if prior["event_type"] != "operator_action_recorded" or prior["payload_json"] != json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False):
                    raise ValueError("This idempotency key was already used for a different incident update.")
                return self._decode(prior)
            if incident["operational_status"] != "open":
                raise ValueError("Reopen the incident before adding an action to its timeline.")
            return self._append(db, incident_id, "operator_action_recorded", at,
                                idempotency_key, payload)

    def close(self, incident_id: str, *, reason: str, context: str | None,
              idempotency_key: str) -> dict[str, Any]:
        payload = {"reason": reason.strip(), "supporting_context": context.strip() if context and context.strip() else None}
        if not payload["reason"]:
            raise ValueError("A closure reason is required.")
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            incident = db.execute("SELECT operational_status FROM incidents WHERE incident_id=?",
                                  (incident_id,)).fetchone()
            if incident is None:
                raise KeyError("Unknown incident")
            prior = db.execute("SELECT * FROM incident_timeline WHERE incident_id=? AND idempotency_key=?",
                               (incident_id, idempotency_key)).fetchone()
            if prior:
                if prior["event_type"] != "incident_closed" or prior["payload_json"] != json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False):
                    raise ValueError("This idempotency key was already used for a different incident update.")
                return self._decode(prior)
            if incident["operational_status"] != "open":
                raise ValueError("This incident is already resolved.")
            now = utc_now()
            event = self._append(db, incident_id, "incident_closed", now, idempotency_key, payload)
            db.execute("UPDATE incidents SET operational_status='resolved' WHERE incident_id=?",
                       (incident_id,))
            return event

    def reopen(self, incident_id: str, *, reason: str, idempotency_key: str) -> dict[str, Any]:
        payload = {"reason": reason.strip()}
        if not payload["reason"]:
            raise ValueError("A reopening reason is required.")
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            incident = db.execute("SELECT operational_status FROM incidents WHERE incident_id=?",
                                  (incident_id,)).fetchone()
            if incident is None:
                raise KeyError("Unknown incident")
            prior = db.execute("SELECT * FROM incident_timeline WHERE incident_id=? AND idempotency_key=?",
                               (incident_id, idempotency_key)).fetchone()
            if prior:
                if prior["event_type"] != "incident_reopened" or prior["payload_json"] != json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False):
                    raise ValueError("This idempotency key was already used for a different incident update.")
                return self._decode(prior)
            if incident["operational_status"] != "resolved":
                raise ValueError("Only a resolved incident can be reopened.")
            now = utc_now()
            event = self._append(db, incident_id, "incident_reopened", now, idempotency_key, payload)
            db.execute("UPDATE incidents SET operational_status='open' WHERE incident_id=?",
                       (incident_id,))
            return event

    def detail(self, incident_id: str) -> dict[str, Any]:
        with self.store._connection() as db:
            incident = db.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
            if incident is None:
                raise KeyError("Unknown incident")
            result = dict(incident)
            result["episode"] = json.loads(result.pop("episode_json"))
            result["statistics"] = self._episode_statistics(db, result)
            result["relationships"] = self._relationships(db, incident_id)
            result["investigations"] = []
            for job in db.execute(
                    "SELECT j.job_id,j.reason,j.cutoff,j.status,j.created_at,j.completed_at,j.error,"
                    "j.cross_category_context_json,"
                    "j.report_json,r.revision FROM incident_jobs j LEFT JOIN incident_report_revisions r "
                    "ON r.job_id=j.job_id WHERE j.incident_id=? ORDER BY COALESCE(r.revision,999999),j.created_at,j.rowid",
                    (incident_id,)).fetchall():
                item = dict(job)
                item["report"] = json.loads(item.pop("report_json")) if item["report_json"] else None
                item["cross_category_context"] = json.loads(item.pop("cross_category_context_json") or "[]")
                result["investigations"].append(item)
            result["actions"] = [self._decode(row) for row in db.execute(
                "SELECT * FROM incident_timeline WHERE incident_id=? AND event_type='operator_action_recorded' "
                "ORDER BY occurred_at,recorded_at", (incident_id,)).fetchall()]
            result["lifecycle_history"] = [self._decode(row) for row in db.execute(
                "SELECT * FROM incident_timeline WHERE incident_id=? AND event_type IN "
                "('incident_closed','incident_reopened') ORDER BY recorded_at", (incident_id,)).fetchall()]
            result["monitoring"] = self._monitoring(db, result)
            result["operator_name"] = self.operator_name
            return result

    @staticmethod
    def _relationships(db: Any, incident_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT g.*,v.version,v.source_incident_id,v.source_job_id,v.assessed_at,"
            "v.status AS assessment_status,v.title,v.explanation,v.supporting_evidence_json "
            "FROM incident_relationship_members m "
            "JOIN incident_relationship_groups g ON g.group_id=m.group_id "
            "JOIN incident_relationship_versions v ON v.group_id=g.group_id AND v.version=g.current_version "
            "WHERE m.incident_id=? ORDER BY g.updated_at DESC,g.group_id",
            (incident_id,),
        ).fetchall()
        relationships: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            support = json.loads(item.pop("supporting_evidence_json") or "{}")
            item["supporting_evidence_by_incident"] = support
            item["members"] = [dict(member) for member in db.execute(
                "SELECT i.incident_id,i.category,i.operational_status FROM incident_relationship_members m "
                "JOIN incidents i ON i.incident_id=m.incident_id WHERE m.group_id=? ORDER BY i.category,i.incident_id",
                (item["group_id"],),
            ).fetchall()]
            report_row = db.execute("SELECT report_json FROM incident_jobs WHERE job_id=?",
                                    (item["source_job_id"],)).fetchone()
            evidence = {}
            if report_row and report_row["report_json"]:
                report = json.loads(report_row["report_json"])
                evidence = {str(record.get("evidence_id")): record for record in report.get("evidence", [])
                            if isinstance(record, dict) and record.get("evidence_id")}
            item["supporting_records"] = {
                member_id: [evidence[value] for value in ids if value in evidence]
                for member_id, ids in support.items()
            }
            item["history"] = [dict(version) for version in db.execute(
                "SELECT version,status,title,explanation,assessed_at,source_incident_id,source_job_id "
                "FROM incident_relationship_versions WHERE group_id=? ORDER BY version",
                (item["group_id"],),
            ).fetchall()]
            relationships.append(item)
        return relationships

    @staticmethod
    def _episode_statistics(db: Any, incident: dict[str, Any]) -> dict[str, Any]:
        episode = incident["episode"]
        row = db.execute(
            "SELECT COUNT(*) AS windows,COALESCE(SUM(CAST(json_extract(payload_json,'$.ticket_count') AS INTEGER)),0) AS tickets,"
            "COALESCE(SUM(CAST(json_extract(payload_json,'$.expected') AS REAL)),0) AS expected "
            "FROM events WHERE run_id=? AND event_type='anomaly_alert' "
            "AND json_extract(payload_json,'$.category')=? "
            "AND CAST(json_extract(payload_json,'$.window_index') AS INTEGER) BETWEEN ? AND ?",
            (incident["stream_id"], incident["category"],
             int(episode.get("start_window", 0)),
             int(episode.get("latest_window", episode.get("start_window", 0)))),
        ).fetchone()
        return {"alert_windows": int(row["windows"]), "tickets": int(row["tickets"]),
                "expected": float(row["expected"]), "peak_window_tickets": episode.get("peak_ticket_count")}

    @staticmethod
    def _monitoring(db: Any, incident: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        start_index = int(incident["episode"].get("latest_window", 0))
        rows = db.execute(
            "SELECT o.window_index,o.observation_json,o.result_json FROM detector_host_observations o "
            "WHERE o.stream_id=? AND o.window_index>? ORDER BY o.window_index DESC LIMIT 12",
            (incident["stream_id"], start_index),
        ).fetchall()
        categories = []
        for row in reversed(rows):
            observation = json.loads(row["observation_json"])
            result = json.loads(row["result_json"])
            category = incident["category"]
            score = result.get("categories", {}).get(category, {})
            categories.append({"window_index": row["window_index"],
                               "window_start": observation.get("window_start"),
                               "window_end": observation.get("window_end"),
                               "tickets": observation.get("category_counts", {}).get(category),
                               "usual": score.get("expected")})
        logs = db.execute(
            "SELECT simulated_at,payload_json FROM events WHERE run_id=? AND source='product_logs' "
            "AND event_type='service_log_summary' AND simulated_at>=? ORDER BY sequence DESC LIMIT 30",
            (incident["stream_id"], incident["opened_at"]),
        ).fetchall()
        service_evidence = []
        category_terms = {incident["category"], "checkout-payments" if incident["category"] == "payments" else incident["category"]}
        for row in logs:
            payload = json.loads(row["payload_json"])
            description = f"{payload.get('service', '')} {payload.get('dependency', '')} {payload.get('message', '')}".casefold()
            if not any(term in description for term in category_terms):
                continue
            service_evidence.append({"observed_at": row["simulated_at"], "service": payload.get("service"),
                                     "level": payload.get("level"), "message": payload.get("message"),
                                     "errors": payload.get("errors"), "requests": payload.get("requests")})
            if len(service_evidence) == 8:
                break
        return {"ticket_windows": categories, "service_health": service_evidence}
