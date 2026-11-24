"""Persisted incident investigations with a frozen view of run evidence."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from agentic_ops.evidence import EvidenceTools
from agentic_ops.search_activity import summarize_search_trace
from agentic_ops.store import EventStore, utc_now


class ScopedStore:
    def __init__(self, store: EventStore, run_id: str, cutoff: str, sequence: int):
        self._store, self.run_id, self.cutoff, self.sequence = store, run_id, cutoff, sequence

    def __getattr__(self, name: str) -> Any:
        if name in {"cache_embeddings", "get_cached_embeddings"}:
            return getattr(self._store, name)
        raise AttributeError(name)

    def search_events(self, run_id: str, *args: Any, **kwargs: Any) -> list[dict]:
        if run_id != self.run_id:
            raise ValueError("Evidence requested outside investigation scope")
        return self._store.search_events(run_id, *args, **kwargs,
                                         max_sequence=self.sequence, as_of=self.cutoff)

    def get_events_by_evidence_ids(self, run_id: str, evidence_ids: list[str]) -> list[dict]:
        if run_id != self.run_id:
            raise ValueError("Evidence requested outside investigation scope")
        return self._store.get_events_by_evidence_ids(run_id, evidence_ids,
                                                       max_sequence=self.sequence, as_of=self.cutoff)


class ScopedCorpus:
    def __init__(self, corpus: Any, cutoff: str):
        self.corpus, self.cutoff = corpus, cutoff

    def __getattr__(self, name: str) -> Any:
        return getattr(self.corpus, name)

    def search(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["cutoff"] = min(kwargs.get("cutoff") or self.cutoff, self.cutoff)
        return self.corpus.search(*args, **kwargs)

    def search_structured(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["cutoff"] = min(kwargs.get("cutoff") or self.cutoff, self.cutoff)
        return self.corpus.search_structured(*args, **kwargs)


def _snapshot_cross_category_context(db: Any, stream_id: str, current_incident_id: str,
                                    cutoff: str, sequence_bound: int) -> list[dict[str, Any]]:
    """Freeze a compact index of other open incidents observable to this job."""
    current = db.execute("SELECT category FROM incidents WHERE incident_id=?", (current_incident_id,)).fetchone()
    current_category = current["category"] if current else None
    incidents = db.execute(
        "SELECT * FROM incidents WHERE stream_id=? AND incident_id<>? "
        "AND operational_status='open' ORDER BY opened_at,incident_id",
        (stream_id, current_incident_id),
    ).fetchall()
    alerts = db.execute(
        "SELECT sequence,simulated_at,payload_json FROM events WHERE run_id=? "
        "AND source='detector' AND event_type='anomaly_alert' AND sequence<=? ORDER BY sequence",
        (stream_id, sequence_bound),
    ).fetchall()
    alert_payloads = [(int(row["sequence"]), row["simulated_at"], json.loads(row["payload_json"]))
                      for row in alerts]
    result: list[dict[str, Any]] = []
    for incident in incidents:
        if incident["category"] == current_category:
            continue
        episode = json.loads(incident["episode_json"])
        incident_start = int(episode.get("start_window", 0))
        incident_end = int(episode.get("latest_window", incident_start))
        episode_alerts = [item for item in alert_payloads
                          if item[2].get("category") == incident["category"]
                          and incident_start <= int(item[2].get("window_index", 0)) <= incident_end
                          and item[2].get("window_end", item[1]) <= cutoff]
        if not episode_alerts:
            continue
        end_index = max(int(item[2].get("window_index", 0)) for item in episode_alerts)
        start_index = min(int(item[2].get("window_index", 0)) for item in episode_alerts)
        latest_report = db.execute(
            "SELECT j.cutoff,j.sequence_bound,j.report_json FROM incident_jobs j "
            "JOIN incident_report_revisions r ON r.job_id=j.job_id "
            "WHERE j.incident_id=? AND j.status='completed' AND j.cutoff<=? AND j.sequence_bound<=? "
            "ORDER BY r.revision DESC LIMIT 1",
            (incident["incident_id"], cutoff, sequence_bound),
        ).fetchone()
        latest_finding = None
        finding_cutoff = None
        if latest_report and latest_report["report_json"]:
            report = json.loads(latest_report["report_json"])
            latest_finding = str(report.get("summary", "")).strip()[:500] or None
            finding_cutoff = latest_report["cutoff"]
        result.append({
            "incident_id": incident["incident_id"],
            "category": incident["category"],
            "episode_status": incident["status"],
            "operational_status": incident["operational_status"],
            "episode_start_window": start_index,
            "episode_end_window": end_index,
            "episode_started_at": min(str(item[2].get("window_start", item[1])) for item in episode_alerts),
            "latest_window_end": max(str(item[2].get("window_end", item[1])) for item in episode_alerts),
            "alert_windows": len(episode_alerts),
            "tickets": sum(int(item[2].get("ticket_count", 0)) for item in episode_alerts),
            "latest_finding": latest_finding,
            "finding_cutoff": finding_cutoff,
        })
    return result


def schedule_job(store: EventStore, incident_id: str, reason: str, idempotency_key: str) -> dict:
    """Record one immutable snapshot; retries and concurrent duplicate requests reuse a job."""
    if not idempotency_key.strip():
        raise ValueError("An idempotency key is required")
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"incident-job:{incident_id}:{idempotency_key}"))
    with store._connection() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM incident_jobs WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            return {**dict(existing), "request_reused": True}
        incident = db.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        if not incident:
            raise KeyError("Unknown incident")
        prior_jobs = db.execute("SELECT * FROM incident_jobs WHERE incident_id=? ORDER BY queue_order,rowid",
                                (incident_id,)).fetchall()
        if prior_jobs and not any(job["status"] == "completed" for job in prior_jobs):
            first_pending = next((job for job in prior_jobs if job["reason"] == "initial alert"
                                  and job["status"] in {"queued", "running"}), None)
            if first_pending:
                return {**dict(first_pending), "request_reused": True}
        initial = not prior_jobs
        # The legacy detector event is written after the episode event and is
        # needed by the existing evidence tools to construct its summary.
        episode = json.loads(incident["episode_json"])
        if initial:
            opening = db.execute("SELECT payload_json FROM episode_outbox WHERE episode_id=? AND stream_id=? "
                                 "AND event_type='episode_opened' ORDER BY window_index LIMIT 1",
                                 (incident_id, incident["stream_id"])).fetchone()
            if opening is None:
                raise ValueError("Initial alert snapshot is unavailable")
            episode = json.loads(opening["payload_json"])["episode"]
            reason = "initial alert"
        alert = db.execute("SELECT * FROM events WHERE run_id=? AND event_type='anomaly_alert' "
                           "AND json_extract(payload_json,'$.category')=? "
                           "AND json_extract(payload_json,'$.window_index')=? ORDER BY sequence DESC LIMIT 1",
                           (incident["stream_id"], incident["category"], episode["latest_window"])).fetchone()
        if alert is None:
            raise ValueError("Incident evidence is not ready")
        seq = alert["sequence"] if initial else db.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?",
                                                         (incident["stream_id"],)).fetchone()[0]
        cutoff = json.loads(alert["payload_json"])["window_end"]
        if reason != "initial alert":
            latest = db.execute("SELECT MAX(simulated_at) FROM events WHERE run_id=? AND sequence<=? "
                                "AND source NOT IN ('knowledge_docs','runbooks')",
                                (incident["stream_id"], seq)).fetchone()[0]
            cutoff = max(cutoff, latest or cutoff)
        cross_category_context = _snapshot_cross_category_context(
            db, incident["stream_id"], incident_id, cutoff, seq)
        # A new browser click gets a new idempotency key. Collapse concurrent
        # submissions for the same snapshot, but allow an operator to rerun a
        # completed snapshot deliberately (for example after changing models).
        duplicate = db.execute(
            "SELECT * FROM incident_jobs WHERE incident_id=? AND reason=? AND cutoff=? "
            "AND sequence_bound=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (incident_id, reason, cutoff, seq),
        ).fetchone()
        if duplicate:
            return {**dict(duplicate), "request_reused": True}
        order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
        db.execute("INSERT INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,"
                   "episode_json,status,cross_category_context_json,created_at,report_json,error,queue_order,queued_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, incident_id, incident["stream_id"], reason, cutoff, seq,
                    json.dumps(episode), "queued", json.dumps(cross_category_context, sort_keys=True),
                    utc_now(), None, None, order, utc_now()))
        return {**dict(db.execute("SELECT * FROM incident_jobs WHERE job_id=?", (job_id,)).fetchone()),
                "request_reused": False}


class IncidentWorker:
    def __init__(self, store: EventStore, investigator: Any, evidence: EvidenceTools,
                 clock: Any = None, lease_seconds: int | None = None, cooldown_seconds: int | None = None,
                 max_followups: int | None = None, max_attempts: int | None = None):
        self.store, self.investigator, self.evidence = store, investigator, evidence
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = max(3, int(os.getenv("AOI_JOB_LEASE_SECONDS", "120")) if lease_seconds is None else lease_seconds)
        self.cooldown_seconds = int(os.getenv("AOI_FOLLOWUP_COOLDOWN_SECONDS", "600")) if cooldown_seconds is None else cooldown_seconds
        self.max_followups = int(os.getenv("AOI_MAX_AUTOMATIC_FOLLOWUPS", "2")) if max_followups is None else max_followups
        self.max_attempts = max(1, int(os.getenv("AOI_JOB_MAX_ATTEMPTS", "2")) if max_attempts is None else max_attempts)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="incident-worker")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=30)

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.run_one():
                self._stop.wait(0.5)

    def run_one(self) -> bool:
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            now_text = now.isoformat()
            expired = db.execute("SELECT * FROM incident_jobs WHERE status='running' AND "
                                 "(lease_until IS NULL OR lease_until<=?) ORDER BY queue_order", (now_text,)).fetchall()
            for old in expired:
                if old["attempt_count"] < self.max_attempts:
                    order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
                    db.execute("UPDATE incident_jobs SET status='queued',queue_order=?,queued_at=?,lease_until=NULL,"
                               "error='Lease expired; provider execution may have completed.' WHERE job_id=?",
                               (order, now_text, old["job_id"]))
                else:
                    db.execute("UPDATE incident_jobs SET status='failed',lease_until=NULL,"
                               "error='Investigation lease expired; review required.' WHERE job_id=?", (old["job_id"],))
            self._schedule_pending(db, now)
            if db.execute("SELECT 1 FROM incident_jobs WHERE status='running' LIMIT 1").fetchone():
                return False
            row = db.execute("SELECT * FROM incident_jobs WHERE status='queued' ORDER BY queue_order, rowid LIMIT 1").fetchone()
            if row is None:
                return False
            job = dict(row)
            job["fence_token"] += 1
            job["attempt_count"] += 1
            db.execute("UPDATE incident_jobs SET status='running',attempt_count=?,fence_token=?,lease_until=?,error=NULL WHERE job_id=?",
                       (job["attempt_count"], job["fence_token"], (now + timedelta(seconds=self.lease_seconds)).isoformat(), job["job_id"]))
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job, heartbeat_stop), daemon=True)
        heartbeat.start()
        try:
            self._execute(job)
        except Exception as exc:
            with self.store._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM incident_jobs WHERE job_id=? AND status='running' AND fence_token=? AND lease_until>?",
                                     (job["job_id"], job["fence_token"], self.clock().isoformat())).fetchone()
                if current:
                    order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid if job["attempt_count"] < self.max_attempts else current["queue_order"]
                    db.execute("UPDATE incident_jobs SET status=?,error=?,lease_until=NULL,queue_order=?,queued_at=? WHERE job_id=? AND fence_token=?",
                               ("queued" if job["attempt_count"] < self.max_attempts else "failed", str(exc), order, self.clock().isoformat(), job["job_id"], job["fence_token"]))
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)
        return True

    def _heartbeat(self, job: dict, stop: threading.Event) -> None:
        while not stop.wait(self.lease_seconds / 3):
            with self.store._connection() as db:
                now = self.clock()
                updated = db.execute("UPDATE incident_jobs SET lease_until=? WHERE job_id=? AND status='running' "
                                     "AND fence_token=? AND lease_until>?",
                                     ((now + timedelta(seconds=self.lease_seconds)).isoformat(), job["job_id"],
                                      job["fence_token"], now.isoformat()))
                if not updated.rowcount:
                    return

    def _schedule_pending(self, db: Any, now: datetime) -> None:
        for pending in db.execute("SELECT * FROM pending_investigation_followups ORDER BY rowid").fetchall():
            jobs = db.execute("SELECT * FROM incident_jobs WHERE incident_id=? ORDER BY queue_order DESC",
                              (pending["incident_id"],)).fetchall()
            if any(job["status"] in {"queued", "running"} for job in jobs):
                continue
            if sum(job["automatic_followup"] for job in jobs) >= self.max_followups:
                continue
            completed = [datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
                         for job in jobs if job["completed_at"]]
            if not completed:
                continue
            if now < max(completed) + timedelta(seconds=self.cooldown_seconds):
                continue
            incident = db.execute("SELECT episode_json FROM incidents WHERE incident_id=?", (pending["incident_id"],)).fetchone()
            latest = db.execute("SELECT COALESCE(MAX(sequence),0),MAX(simulated_at) FROM events WHERE run_id=? "
                                "AND source NOT IN ('knowledge_docs','runbooks')", (pending["stream_id"],)).fetchone()
            cutoff = max(pending["cutoff"], latest[1] or pending["cutoff"])
            sequence_bound = max(pending["sequence_bound"], latest[0])
            cross_category_context = _snapshot_cross_category_context(
                db, pending["stream_id"], pending["incident_id"], cutoff, sequence_bound)
            order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
            job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"followup:{pending['incident_id']}:{pending['event_id']}"))
            db.execute("INSERT OR IGNORE INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,"
                       "episode_json,status,cross_category_context_json,created_at,queue_order,queued_at,automatic_followup) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
                       (job_id, pending["incident_id"], pending["stream_id"], "Relevant service or issue update",
                        cutoff, sequence_bound, incident["episode_json"] if incident else pending["episode_json"],
                        "queued", json.dumps(cross_category_context, sort_keys=True), now.isoformat(), order,
                        now.isoformat()))
            db.execute("DELETE FROM pending_investigation_followups WHERE incident_id=?", (pending["incident_id"],))

    def _execute(self, job: dict) -> None:
        scoped = ScopedStore(self.store, job["stream_id"], job["cutoff"], job["sequence_bound"])
        tools = EvidenceTools(scoped, retriever=self.evidence.retriever,
                              corpus=ScopedCorpus(self.evidence.corpus, job["cutoff"]), past_only_focus=True)
        episode = json.loads(job["episode_json"])
        category = episode["category"]
        start, end = int(episode["start_window"]), int(episode["latest_window"])
        summary = tools.build_episode_summary(job["stream_id"], category, start, end, end)
        summary["analysis_end_at"] = job["cutoff"]
        alerts = scoped.search_events(job["stream_id"], ("detector",), limit=1000,
                                      event_types=("anomaly_alert",))
        alert = next(item for item in reversed(alerts)
                     if item["payload"].get("category") == category
                     and item["payload"].get("window_index") == end)
        signal = {"evidence_id": alert["evidence_id"], "simulated_at": alert["simulated_at"],
                  **alert["payload"], "episode_start_window_index": start,
                  "episode_end_window_index": end, "focus_window_index": end, "episode_summary": summary}
        context = {"selected_signals": [signal], "categories": [category],
                   "episode_summaries": [summary],
                   "cross_category_context": json.loads(job.get("cross_category_context_json") or "[]"),
                   **signal}
        investigator = self.investigator()
        report = investigator.investigate(job["stream_id"], "What is driving this ticket spike?", tools, context)
        report.episode_summaries = [summary]
        report.search_activity = summarize_search_trace(report.tool_trace)
        payload = report.to_dict()
        payload["investigator_mode"] = getattr(investigator, "mode", "custom")
        payload["evidence_cutoff"] = job["cutoff"]
        payload["evidence_sequence"] = job["sequence_bound"]
        self._commit_report(job, payload)

    def _commit_report(self, job: dict, payload: dict) -> bool:
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock().isoformat()
            result = db.execute("UPDATE incident_jobs SET status='completed',report_json=?,completed_at=?,lease_until=NULL "
                       "WHERE job_id=? AND status='running' AND fence_token=? AND lease_until>? AND report_json IS NULL",
                       (json.dumps(payload), now, job["job_id"], job["fence_token"], now))
            if not result.rowcount:
                return False
            incident_id = db.execute("SELECT incident_id FROM incident_jobs WHERE job_id=?", (job["job_id"],)).fetchone()[0]
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM incident_report_revisions WHERE incident_id=?",
                                  (incident_id,)).fetchone()[0]
            db.execute("INSERT INTO incident_report_revisions VALUES (?,?,?)", (incident_id, revision, job["job_id"]))
            snapshot = db.execute("SELECT cutoff,sequence_bound FROM incident_jobs WHERE job_id=?", (job["job_id"],)).fetchone()
            db.execute("DELETE FROM pending_investigation_followups WHERE incident_id=? AND cutoff<=? AND sequence_bound<=?",
                       (incident_id, snapshot["cutoff"], snapshot["sequence_bound"]))
            return True
