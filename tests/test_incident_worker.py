import tempfile
import time
import unittest
from pathlib import Path

from agentic_ops.agent import Investigator
from agentic_ops.domain import RCAReport, SimulationConfig
from agentic_ops.incident_worker import ScopedStore, schedule_job
from agentic_ops.service import OperationsRuntime
from agentic_ops.store import EventStore


class SnapshotInvestigator(Investigator):
    def investigate(self, run_id, question, evidence_tools, alert_context=None, on_progress=None):
        records = evidence_tools.store.search_events(run_id, ("support_tickets",), limit=1000)
        cutoff = alert_context["episode_summary"]["analysis_end_at"]
        assert all(record["simulated_at"] <= cutoff for record in records)
        return RCAReport("completed", "Snapshot reviewed", [], [])


class IncidentWorkerTests(unittest.TestCase):
    def test_scope_excludes_future_and_late_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "scope.db")
            run_id = store.create_run("healthy", 1, "poisson", {})
            before = store.add_event(run_id, "2026-01-01T10:00:00Z", "support_tickets", "ticket_created", {"body": "before"})
            future = store.add_event(run_id, "2026-01-01T12:00:00Z", "support_tickets", "ticket_created", {"body": "future"})
            scoped = ScopedStore(store, run_id, "2026-01-01T11:00:00Z", future["sequence"])
            late = store.add_event(run_id, "2026-01-01T09:00:00Z", "support_tickets", "ticket_created", {"body": "late import"})
            self.assertEqual([item["evidence_id"] for item in scoped.search_events(run_id, ("support_tickets",))],
                             [before["evidence_id"]])
            self.assertEqual([item["evidence_id"] for item in scoped.get_events_by_evidence_ids(
                run_id, [before["evidence_id"], future["evidence_id"], late["evidence_id"]])], [before["evidence_id"]])

    def test_automatic_initial_job_and_idempotent_manual_request(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = OperationsRuntime(EventStore(Path(directory) / "runtime.db"), SnapshotInvestigator())
            try:
                run = runtime.start_run(SimulationConfig(window_count=52, calibration_windows=20, tick_delay_seconds=0))
                self.assertEqual(runtime.wait_for_run(run["run_id"], timeout=40)["status"], "completed")
                with runtime.store._connection() as db:
                    initial = db.execute("SELECT * FROM incident_jobs WHERE reason='initial alert' LIMIT 1").fetchone()
                self.assertIsNotNone(initial)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with runtime.store._connection() as db:
                        initial = db.execute("SELECT * FROM incident_jobs WHERE job_id=?", (initial["job_id"],)).fetchone()
                    if initial["status"] in ("completed", "failed", "blocked"):
                        break
                    time.sleep(0.05)
                self.assertEqual(initial["status"], "completed", initial["error"])
                first = schedule_job(runtime.store, initial["incident_id"], "Review", "request-1")
                second = schedule_job(runtime.store, initial["incident_id"], "Review", "request-1")
                self.assertEqual(first["job_id"], second["job_id"])
            finally:
                runtime.incident_worker.stop()
