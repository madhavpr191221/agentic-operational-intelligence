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


def request_comparison(store: EventStore, incident_ids: list[str], idempotency_key: str) -> dict[str, Any]:
    """Queue a durable, same-replay comparison without changing incident reports."""
    if not idempotency_key.strip():
        raise ValueError("An idempotency key is required")
    selected = list(dict.fromkeys(str(value) for value in incident_ids))
    if len(selected) < 2 or len(selected) > 4:
        raise ValueError("Select between two and four incidents to compare")
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"incident-comparison:{idempotency_key}"))
    with store._connection() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM incident_comparison_jobs WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            if json.loads(existing["selected_incident_ids_json"]) != selected:
                raise ValueError("This idempotency key was already used for a different comparison")
            return {**dict(existing), "request_reused": True}
        placeholders = ",".join("?" for _ in selected)
        incidents = db.execute(f"SELECT * FROM incidents WHERE incident_id IN ({placeholders})", tuple(selected)).fetchall()
        if len(incidents) != len(selected):
            raise KeyError("One or more selected incidents no longer exist")
        by_id = {row["incident_id"]: row for row in incidents}
        streams = {row["stream_id"] for row in incidents}
        categories = {row["category"] for row in incidents}
        if len(streams) != 1:
            raise ValueError("All selected incidents must belong to the same replay")
        if len(categories) < 2:
            raise ValueError("Select incidents from at least two categories")
        stream_id = next(iter(streams))
        latest = db.execute("SELECT COALESCE(MAX(sequence),0),MAX(simulated_at) FROM events WHERE run_id=? "
                            "AND source NOT IN ('knowledge_docs','runbooks')", (stream_id,)).fetchone()
        sequence_bound = int(latest[0])
        cutoff = str(latest[1] or utc_now())
        alerts = db.execute("SELECT sequence,simulated_at,payload_json FROM events WHERE run_id=? "
                            "AND source='detector' AND event_type='anomaly_alert' AND sequence<=? ORDER BY sequence",
                            (stream_id, sequence_bound)).fetchall()
        alert_payloads = [(int(row["sequence"]), row["simulated_at"], json.loads(row["payload_json"]))
                          for row in alerts]
        scope: list[dict[str, Any]] = []
        for incident_id in selected:
            incident = by_id[incident_id]
            episode = json.loads(incident["episode_json"])
            start = int(episode.get("start_window", 0))
            end = int(episode.get("latest_window", start))
            relevant = [item for item in alert_payloads if item[2].get("category") == incident["category"]
                        and start <= int(item[2].get("window_index", 0)) <= end
                        and item[2].get("window_end", item[1]) <= cutoff]
            if not relevant:
                raise ValueError(f"No eligible alert evidence is available for {incident['category']}")
            latest_report = db.execute(
                "SELECT j.cutoff,j.report_json FROM incident_jobs j JOIN incident_report_revisions r ON r.job_id=j.job_id "
                "WHERE j.incident_id=? AND j.status='completed' AND j.cutoff<=? AND j.sequence_bound<=? "
                "ORDER BY r.revision DESC LIMIT 1", (incident_id, cutoff, sequence_bound),
            ).fetchone()
            finding = None
            finding_cutoff = None
            if latest_report and latest_report["report_json"]:
                finding = str(json.loads(latest_report["report_json"]).get("summary", ""))[:500] or None
                finding_cutoff = latest_report["cutoff"]
            scope.append({
                "incident_id": incident_id, "category": incident["category"],
                "episode_status": incident["status"], "operational_status": incident["operational_status"],
                "episode_start_window": min(int(item[2].get("window_index", 0)) for item in relevant),
                "episode_end_window": max(int(item[2].get("window_index", 0)) for item in relevant),
                "episode_started_at": min(str(item[2].get("window_start", item[1])) for item in relevant),
                "latest_window_end": max(str(item[2].get("window_end", item[1])) for item in relevant),
                "alert_windows": len(relevant),
                "tickets": sum(int(item[2].get("ticket_count", 0)) for item in relevant),
                "latest_finding": finding, "finding_cutoff": finding_cutoff,
            })
        now = utc_now()
        order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
        db.execute("INSERT INTO incident_comparison_jobs "
                   "(job_id,stream_id,idempotency_key,selected_incident_ids_json,scope_json,cutoff,sequence_bound,status,created_at,queue_order,queued_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, stream_id, idempotency_key, json.dumps(selected), json.dumps(scope, sort_keys=True),
                    cutoff, sequence_bound, "queued", now, order, now))
        return {**dict(db.execute("SELECT * FROM incident_comparison_jobs WHERE job_id=?", (job_id,)).fetchone()),
                "request_reused": False}


def list_comparison_jobs(store: EventStore, stream_id: str | None = None) -> list[dict[str, Any]]:
    with store._connection() as db:
        query = "SELECT * FROM incident_comparison_jobs"
        params: tuple[Any, ...] = ()
        if stream_id is not None:
            query += " WHERE stream_id=?"
            params = (stream_id,)
        rows = db.execute(query + " ORDER BY created_at DESC,rowid DESC", params).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["selected_incident_ids"] = json.loads(item.pop("selected_incident_ids_json"))
            item["scope"] = json.loads(item.pop("scope_json"))
            item["report"] = json.loads(item.pop("report_json")) if item.get("report_json") else None
            items.append(item)
        return items


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
            expired = db.execute("SELECT 'incident' AS job_kind,j.* FROM incident_jobs j WHERE j.status='running' AND "
                                 "(lease_until IS NULL OR lease_until<=?) ORDER BY queue_order", (now_text,)).fetchall()
            expired += db.execute("SELECT 'comparison' AS job_kind,j.* FROM incident_comparison_jobs j WHERE j.status='running' AND "
                                  "(lease_until IS NULL OR lease_until<=?) ORDER BY queue_order", (now_text,)).fetchall()
            for old in expired:
                table = "incident_jobs" if old["job_kind"] == "incident" else "incident_comparison_jobs"
                if old["attempt_count"] < self.max_attempts:
                    order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
                    db.execute(f"UPDATE {table} SET status='queued',queue_order=?,queued_at=?,lease_until=NULL,"
                               "error='Lease expired; provider execution may have completed.' WHERE job_id=?",
                               (order, now_text, old["job_id"]))
                else:
                    db.execute(f"UPDATE {table} SET status='failed',lease_until=NULL,"
                               "error='Investigation lease expired; review required.' WHERE job_id=?", (old["job_id"],))
            self._schedule_pending(db, now)
            if db.execute("SELECT 1 FROM incident_jobs WHERE status='running' UNION ALL "
                           "SELECT 1 FROM incident_comparison_jobs WHERE status='running' LIMIT 1").fetchone():
                return False
            row = db.execute("SELECT job_kind,job_id,queue_order,row_order FROM ("
                             "SELECT 'incident' AS job_kind,job_id,queue_order,rowid AS row_order FROM incident_jobs WHERE status='queued' "
                             "UNION ALL SELECT 'comparison',job_id,queue_order,rowid FROM incident_comparison_jobs WHERE status='queued') "
                             "ORDER BY queue_order,row_order LIMIT 1").fetchone()
            if row is None:
                return False
            table = "incident_jobs" if row["job_kind"] == "incident" else "incident_comparison_jobs"
            job = dict(db.execute(f"SELECT * FROM {table} WHERE job_id=?", (row["job_id"],)).fetchone())
            job["job_kind"] = row["job_kind"]
            job["fence_token"] += 1
            job["attempt_count"] += 1
            db.execute(f"UPDATE {table} SET status='running',attempt_count=?,fence_token=?,lease_until=?,error=NULL WHERE job_id=?",
                       (job["attempt_count"], job["fence_token"], (now + timedelta(seconds=self.lease_seconds)).isoformat(), job["job_id"]))
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job, heartbeat_stop), daemon=True)
        heartbeat.start()
        try:
            self._execute(job)
        except Exception as exc:
            with self.store._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                table = "incident_jobs" if job.get("job_kind") == "incident" else "incident_comparison_jobs"
                current = db.execute(f"SELECT * FROM {table} WHERE job_id=? AND status='running' AND fence_token=? AND lease_until>?",
                                     (job["job_id"], job["fence_token"], self.clock().isoformat())).fetchone()
                if current:
                    order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid if job["attempt_count"] < self.max_attempts else current["queue_order"]
                    db.execute(f"UPDATE {table} SET status=?,error=?,lease_until=NULL,queue_order=?,queued_at=? WHERE job_id=? AND fence_token=?",
                               ("queued" if job["attempt_count"] < self.max_attempts else "failed", str(exc), order, self.clock().isoformat(), job["job_id"], job["fence_token"]))
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)
        return True

    def _heartbeat(self, job: dict, stop: threading.Event) -> None:
        while not stop.wait(self.lease_seconds / 3):
            with self.store._connection() as db:
                now = self.clock()
                table = "incident_jobs" if job.get("job_kind") == "incident" else "incident_comparison_jobs"
                updated = db.execute(f"UPDATE {table} SET lease_until=? WHERE job_id=? AND status='running' "
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
        if job.get("job_kind") == "comparison":
            self._execute_comparison(job)
            return
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
        primary_incident_context = {
            "incident_id": job["incident_id"], "category": category,
            "episode_status": episode.get("status", "active"),
            "operational_status": "open",
            "episode_started_at": summary["start_at"],
            "latest_window_end": summary["end_at"],
            "alert_windows": summary["alert_window_count"],
            "tickets": summary["total_tickets"],
        }
        context = {"selected_signals": [signal], "categories": [category],
                   "episode_summaries": [summary],
                   "cross_category_context": json.loads(job.get("cross_category_context_json") or "[]"),
                   "primary_incident_context": primary_incident_context,
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

    def _execute_comparison(self, job: dict[str, Any]) -> None:
        scoped = ScopedStore(self.store, job["stream_id"], job["cutoff"], job["sequence_bound"])
        tools = EvidenceTools(scoped, retriever=self.evidence.retriever,
                              corpus=ScopedCorpus(self.evidence.corpus, job["cutoff"]), past_only_focus=True)
        scope = json.loads(job["scope_json"])
        alerts = scoped.search_events(job["stream_id"], ("detector",), limit=2000,
                                      event_types=("anomaly_alert",))
        signals: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        for selected in scope:
            category = selected["category"]
            start, end = int(selected["episode_start_window"]), int(selected["episode_end_window"])
            summary = tools.build_episode_summary(job["stream_id"], category, start, end, end)
            summary["analysis_end_at"] = job["cutoff"]
            eligible = [item for item in alerts if item["payload"].get("category") == category
                        and start <= int(item["payload"].get("window_index", -1)) <= end]
            if not eligible:
                raise ValueError(f"No cutoff-eligible alert evidence for selected {category} incident")
            alert = eligible[-1]
            signals.append({"incident_id": selected["incident_id"], "evidence_id": alert["evidence_id"],
                            "simulated_at": alert["simulated_at"], **alert["payload"],
                            "episode_start_window_index": start, "episode_end_window_index": end,
                            "focus_window_index": end, "episode_summary": summary})
            summaries.append(summary)
        primary = scope[0]
        primary_context = dict(primary)
        cross_context = [dict(item) for item in scope[1:]]
        context = {"selected_signals": signals, "categories": list(dict.fromkeys(item["category"] for item in scope)),
                   "episode_summaries": summaries, "primary_incident_context": primary_context,
                   "cross_category_context": cross_context, "comparison_requested": True}
        investigator = self.investigator()
        question = ("Compare the selected category incidents using evidence available by this fixed cutoff. "
                    "Keep findings scoped to their separate incidents. Assess a shared explanation only when "
                    "independent underlying evidence supports it; otherwise explain what remains uncertain.")
        report = investigator.investigate(job["stream_id"], question, tools, context)
        report.episode_summaries = summaries
        report.search_activity = summarize_search_trace(report.tool_trace)
        payload = report.to_dict()
        payload["investigator_mode"] = getattr(investigator, "mode", "custom")
        payload["evidence_cutoff"] = job["cutoff"]
        payload["evidence_sequence"] = job["sequence_bound"]
        payload["comparison_scope"] = scope
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE incident_comparison_jobs SET status='completed',report_json=?,completed_at=?,"
                                "lease_until=NULL,error=NULL WHERE job_id=? AND status='running' AND fence_token=? AND lease_until>?",
                                (json.dumps(payload), self.clock().isoformat(), job["job_id"], job["fence_token"],
                                 self.clock().isoformat()))
            if result.rowcount != 1:
                return

    def _commit_report(self, job: dict, payload: dict) -> bool:
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock().isoformat()
            result = db.execute("UPDATE incident_jobs SET status='completed',report_json=?,completed_at=?,lease_until=NULL "
                       "WHERE job_id=? AND status='running' AND fence_token=? AND lease_until>? AND report_json IS NULL",
                       (json.dumps(payload), now, job["job_id"], job["fence_token"], now))
            if not result.rowcount:
                return False
            committed_job = db.execute("SELECT * FROM incident_jobs WHERE job_id=?", (job["job_id"],)).fetchone()
            incident_id = committed_job["incident_id"]
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM incident_report_revisions WHERE incident_id=?",
                                  (incident_id,)).fetchone()[0]
            db.execute("INSERT INTO incident_report_revisions VALUES (?,?,?)", (incident_id, revision, job["job_id"]))
            _persist_relationship_assessments(db, dict(committed_job), payload, now)
            snapshot = db.execute("SELECT cutoff,sequence_bound FROM incident_jobs WHERE job_id=?", (job["job_id"],)).fetchone()
            db.execute("DELETE FROM pending_investigation_followups WHERE incident_id=? AND cutoff<=? AND sequence_bound<=?",
                       (incident_id, snapshot["cutoff"], snapshot["sequence_bound"]))
            return True


def _persist_relationship_assessments(db: Any, job: dict[str, Any],
                                     report: dict[str, Any], now: str) -> None:
    """Persist only cited relationships whose shared system is observed for every member."""
    primary_id = str(job["incident_id"])
    allowed = {primary_id}
    allowed.update(str(item.get("incident_id")) for item in
                   json.loads(job.get("cross_category_context_json") or "[]")
                   if isinstance(item, dict) and item.get("incident_id"))
    context_rows = db.execute(
        "SELECT incident_id,stream_id,category FROM incidents WHERE incident_id IN ("
        + ",".join("?" for _ in allowed) + ")", tuple(allowed),
    ).fetchall() if allowed else []
    contexts = {row["incident_id"]: dict(row) for row in context_rows}
    report_evidence = {str(item.get("evidence_id")) for item in report.get("evidence", [])
                       if isinstance(item, dict) and item.get("evidence_id")}
    cross_records: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in report.get("tool_trace", []):
        if not isinstance(entry, dict) or entry.get("tool") != "search_cross_category_evidence":
            continue
        incident_id = str(entry.get("arguments", {}).get("incident_id", ""))
        result = entry.get("result", {})
        for record in result.get("records", []) if isinstance(result, dict) else []:
            if isinstance(record, dict) and record.get("evidence_id"):
                cross_records.setdefault(incident_id, {})[str(record["evidence_id"])] = record

    seen_group_ids: set[str] = set()
    for assessment in report.get("relationship_assessments", []):
        if not isinstance(assessment, dict):
            continue
        incident_ids = list(dict.fromkeys(str(value) for value in assessment.get("incident_ids", [])))
        if (len(incident_ids) < 2 or primary_id not in incident_ids
                or len(incident_ids) > 4 or any(value not in allowed for value in incident_ids)):
            continue
        members = [contexts.get(value) for value in incident_ids]
        if any(member is None or member["stream_id"] != job["stream_id"] for member in members):
            continue
        if len({member["category"] for member in members if member}) < 2:
            continue
        shared_system = str(assessment.get("shared_system", "")).strip()
        if not shared_system or len(shared_system) > 160:
            continue
        support = assessment.get("supporting_evidence_by_incident", {})
        if not isinstance(support, dict) or set(support) != set(incident_ids):
            continue
        valid_support: dict[str, list[str]] = {}
        valid = True
        for member_id in incident_ids:
            evidence_ids = list(dict.fromkeys(str(value) for value in support.get(member_id, [])))
            available = cross_records.get(member_id, {})
            if not evidence_ids or not set(evidence_ids).issubset(report_evidence | set(available)):
                valid = False
                break
            if not set(evidence_ids).issubset(available):
                valid = False
                break
            if any(shared_system.casefold() not in json.dumps(available[value].get("payload", {}),
                                                                ensure_ascii=False).casefold()
                   for value in evidence_ids):
                valid = False
                break
            valid_support[member_id] = evidence_ids
        if not valid:
            continue
        status = str(assessment.get("status", "possible"))
        if status not in {"possible", "reaffirmed", "weakened", "withdrawn"}:
            continue
        group_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"aoi:relationship:{job['stream_id']}:{shared_system.casefold()}:{','.join(sorted(incident_ids))}"))
        if group_id in seen_group_ids:
            continue
        seen_group_ids.add(group_id)
        existing = db.execute("SELECT current_version FROM incident_relationship_groups WHERE group_id=?",
                              (group_id,)).fetchone()
        if existing is None and status != "possible":
            continue
        version = (int(existing["current_version"]) if existing else 0) + 1
        db.execute("INSERT OR IGNORE INTO incident_relationship_groups "
                   "(group_id,stream_id,shared_system,status,current_version,created_at,updated_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (group_id, job["stream_id"], shared_system, status, version, now, now))
        db.executemany("INSERT OR IGNORE INTO incident_relationship_members (group_id,incident_id) VALUES (?,?)",
                       [(group_id, value) for value in incident_ids])
        db.execute("INSERT INTO incident_relationship_versions "
                   "(group_id,version,source_incident_id,source_job_id,assessed_at,status,title,explanation,supporting_evidence_json) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (group_id, version, primary_id, job["job_id"], now, status,
                    str(assessment.get("title", "Possible shared operational issue"))[:240],
                    str(assessment.get("explanation", ""))[:1200],
                    json.dumps(valid_support, sort_keys=True)))
        db.execute("UPDATE incident_relationship_groups SET status=?,current_version=?,updated_at=? WHERE group_id=?",
                   (status, version, now, group_id))
