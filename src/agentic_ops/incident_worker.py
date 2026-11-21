"""Persisted incident investigations with a frozen view of run evidence."""
from __future__ import annotations

import json
import threading
import uuid
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
        # The legacy detector event is written after the episode event and is
        # needed by the existing evidence tools to construct its summary.
        episode = json.loads(incident["episode_json"])
        alert = db.execute("SELECT * FROM events WHERE run_id=? AND event_type='anomaly_alert' "
                           "AND json_extract(payload_json,'$.category')=? "
                           "AND json_extract(payload_json,'$.window_index')=? ORDER BY sequence DESC LIMIT 1",
                           (incident["stream_id"], incident["category"], episode["latest_window"])).fetchone()
        if alert is None:
            raise ValueError("Incident evidence is not ready")
        seq = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?",
                         (incident["stream_id"],)).fetchone()[0]
        cutoff = json.loads(alert["payload_json"])["window_end"]
        if reason != "initial alert":
            latest = db.execute("SELECT MAX(simulated_at) FROM events WHERE run_id=? AND sequence<=? "
                                "AND source NOT IN ('knowledge_docs','runbooks')",
                                (incident["stream_id"], seq)).fetchone()[0]
            cutoff = max(cutoff, latest or cutoff)
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
        db.execute("INSERT INTO incident_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, incident_id, incident["stream_id"], reason, cutoff, seq,
                    incident["episode_json"], "queued", utc_now(), None, None,))
        return {**dict(db.execute("SELECT * FROM incident_jobs WHERE job_id=?", (job_id,)).fetchone()),
                "request_reused": False}


class IncidentWorker:
    def __init__(self, store: EventStore, investigator: Any, evidence: EvidenceTools):
        self.store, self.investigator, self.evidence = store, investigator, evidence
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="incident-worker")

    def start(self) -> None:
        with self.store._connection() as db:
            # An interrupted provider call may have been charged. Do not retry it automatically.
            db.execute("UPDATE incident_jobs SET status='blocked', error=? WHERE status='running'",
                       ("The previous worker stopped during an investigation; review before retrying.",))
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
            row = db.execute("SELECT * FROM incident_jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            if row is None:
                return False
            job = dict(row)
            db.execute("UPDATE incident_jobs SET status='running' WHERE job_id=?", (job["job_id"],))
        try:
            self._execute(job)
        except Exception as exc:
            with self.store._connection() as db:
                db.execute("UPDATE incident_jobs SET status='failed',error=? WHERE job_id=? AND status='running'",
                           (str(exc), job["job_id"]))
        return True

    def _execute(self, job: dict) -> None:
        scoped = ScopedStore(self.store, job["stream_id"], job["cutoff"], job["sequence_bound"])
        tools = EvidenceTools(scoped, retriever=self.evidence.retriever,
                              corpus=ScopedCorpus(self.evidence.corpus, job["cutoff"]))
        episode = json.loads(job["episode_json"])
        category = episode["category"]
        start, end = int(episode["start_window"]), int(episode["latest_window"])
        summary = tools.build_episode_summary(job["stream_id"], category, start, end, end)
        alerts = scoped.search_events(job["stream_id"], ("detector",), limit=1000,
                                      event_types=("anomaly_alert",))
        alert = next(item for item in reversed(alerts)
                     if item["payload"].get("category") == category
                     and item["payload"].get("window_index") == end)
        signal = {"evidence_id": alert["evidence_id"], "simulated_at": alert["simulated_at"],
                  **alert["payload"], "episode_start_window_index": start,
                  "episode_end_window_index": end, "focus_window_index": end, "episode_summary": summary}
        context = {"selected_signals": [signal], "categories": [category],
                   "episode_summaries": [summary], **signal}
        investigator = self.investigator()
        report = investigator.investigate(job["stream_id"], "What is driving this ticket spike?", tools, context)
        report.episode_summaries = [summary]
        report.search_activity = summarize_search_trace(report.tool_trace)
        payload = report.to_dict()
        payload["investigator_mode"] = getattr(investigator, "mode", "custom")
        payload["evidence_cutoff"] = job["cutoff"]
        payload["evidence_sequence"] = job["sequence_bound"]
        with self.store._connection() as db:
            db.execute("UPDATE incident_jobs SET status='completed',report_json=? WHERE job_id=? AND status='running'",
                       (json.dumps(payload), job["job_id"]))
