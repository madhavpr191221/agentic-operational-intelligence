import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentic_ops.agent import Investigator
from agentic_ops.domain import RCAReport, SimulationConfig
from agentic_ops.incident_worker import IncidentWorker, ScopedStore, request_comparison, schedule_job, list_comparison_jobs
from agentic_ops.detection import CategoryDetectorSuite, CountObservation
from agentic_ops.detector_host import DetectorHost
from agentic_ops.evidence import EvidenceTools
from agentic_ops.incident_workspace import IncidentWorkspace
from agentic_ops.service import OperationsRuntime
from agentic_ops.store import EventStore


class SnapshotInvestigator(Investigator):
    def investigate(self, run_id, question, evidence_tools, alert_context=None, on_progress=None):
        records = evidence_tools.store.search_events(run_id, ("support_tickets",), limit=1000)
        cutoff = alert_context["episode_summary"]["analysis_end_at"]
        assert all(record["simulated_at"] <= cutoff for record in records)
        return RCAReport("completed", "Snapshot reviewed", [], [])


class IncidentWorkerTests(unittest.TestCase):
    def _queue_fixture(self, store):
        DetectorHost(store, "stream", CategoryDetectorSuite("poisson", 4, ["payments"]))
        with store._connection() as db:
            db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                       "VALUES (?,?,?,?,?,?,?,?)", ("incident", "stream", "payments", "active", "001", "002", "event", '{}'))
            for name in ("first", "second"):
                order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
                db.execute("INSERT INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,episode_json,status,created_at,queue_order) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (name, "incident", "stream", "initial alert", "002", 0, "{}", "queued", "2026-01-01T00:00:00+00:00", order))

    def test_failed_job_retries_at_fifo_tail_and_exhaustion_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            self._queue_fixture(store)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            worker = IncidentWorker(store, None, None, clock=lambda: now)
            seen = []
            def execute(job):
                seen.append(job["job_id"])
                if job["job_id"] == "first":
                    raise RuntimeError("offline provider failure")
                with store._connection() as db:
                    db.execute("UPDATE incident_jobs SET status='completed',completed_at=? WHERE job_id=?",
                               (now.isoformat(), job["job_id"]))
            worker._execute = execute
            self.assertTrue(worker.run_one())
            self.assertTrue(worker.run_one())
            self.assertTrue(worker.run_one())
            self.assertFalse(worker.run_one())
            self.assertEqual(seen, ["first", "second", "first"])
            with store._connection() as db:
                row = db.execute("SELECT * FROM incident_jobs WHERE job_id='first'").fetchone()
                self.assertEqual((row["status"], row["attempt_count"]), ("failed", 2))

    def test_expired_lease_is_recovered_and_running_job_blocks_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            self._queue_fixture(store)
            now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
            worker = IncidentWorker(store, None, None, clock=lambda: now)
            with store._connection() as db:
                db.execute("UPDATE incident_jobs SET status='running',attempt_count=1,fence_token=1,lease_until=? WHERE job_id='first'",
                           ((now + timedelta(seconds=10)).isoformat(),))
            self.assertFalse(worker.run_one())
            now += timedelta(seconds=11)
            seen = []
            def execute(job):
                seen.append(job["job_id"])
                with store._connection() as db:
                    db.execute("UPDATE incident_jobs SET status='completed' WHERE job_id=?", (job["job_id"],))
            worker._execute = execute
            self.assertTrue(worker.run_one())
            self.assertTrue(worker.run_one())
            self.assertEqual(seen, ["second", "first"])
            with store._connection() as db:
                first = db.execute("SELECT attempt_count,fence_token FROM incident_jobs WHERE job_id='first'").fetchone()
                self.assertEqual(tuple(first), (2, 2))

    def test_pending_followup_waits_for_completion_and_cooldown_then_coalesces(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            self._queue_fixture(store)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            worker = IncidentWorker(store, None, None, clock=lambda: now, cooldown_seconds=600)
            with store._connection() as db:
                db.execute("DELETE FROM incident_jobs WHERE job_id='second'")
                db.execute("UPDATE incident_jobs SET status='running' WHERE job_id='first'")
                db.execute("INSERT INTO pending_investigation_followups VALUES (?,?,?,?,?,?)",
                           ("incident", "stream", "005", 1, '{}', "event-1"))
                worker._schedule_pending(db, now)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM incident_jobs").fetchone()[0], 1)
                db.execute("UPDATE incident_jobs SET status='completed',completed_at=? WHERE job_id='first'", (now.isoformat(),))
                db.execute("UPDATE pending_investigation_followups SET cutoff='006',event_id='event-2' WHERE incident_id='incident'")
                worker._schedule_pending(db, now + timedelta(minutes=9))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM incident_jobs").fetchone()[0], 1)
                worker._schedule_pending(db, now + timedelta(minutes=10))
                job = db.execute("SELECT * FROM incident_jobs WHERE automatic_followup=1").fetchone()
                self.assertEqual(job["cutoff"], "006")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM pending_investigation_followups").fetchone()[0], 0)

    def test_expired_or_superseded_attempt_cannot_commit_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            self._queue_fixture(store)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            worker = IncidentWorker(store, None, None, clock=lambda: now)
            with store._connection() as db:
                db.execute("UPDATE incident_jobs SET status='running',fence_token=2,lease_until=? WHERE job_id='first'",
                           ((now + timedelta(seconds=30)).isoformat(),))
            self.assertFalse(worker._commit_report({"job_id": "first", "fence_token": 1}, {"summary": "stale"}))
            now += timedelta(seconds=31)
            self.assertFalse(worker._commit_report({"job_id": "first", "fence_token": 2}, {"summary": "expired"}))
            with store._connection() as db:
                db.execute("UPDATE incident_jobs SET fence_token=3,lease_until=? WHERE job_id='first'",
                           ((now + timedelta(seconds=30)).isoformat(),))
            self.assertTrue(worker._commit_report({"job_id": "first", "fence_token": 3}, {"summary": "current"}))
            self.assertFalse(worker._commit_report({"job_id": "first", "fence_token": 3}, {"summary": "duplicate"}))

    def test_revisions_follow_commit_order_and_do_not_change_when_retry_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            self._queue_fixture(store)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            worker = IncidentWorker(store, None, None, clock=lambda: now)
            with store._connection() as db:
                db.execute("UPDATE incident_jobs SET status='running',fence_token=1,lease_until=?",
                           ((now + timedelta(seconds=30)).isoformat(),))
            self.assertTrue(worker._commit_report({"job_id": "second", "fence_token": 1}, {"summary": "earlier completion"}))
            self.assertTrue(worker._commit_report({"job_id": "first", "fence_token": 1}, {"summary": "later retry"}))
            with store._connection() as db:
                rows = db.execute("SELECT revision,job_id FROM incident_report_revisions ORDER BY revision").fetchall()
                self.assertEqual([tuple(row) for row in rows], [(1, "second"), (2, "first")])

    def test_automatic_followup_cap_retains_pending_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            self._queue_fixture(store)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            worker = IncidentWorker(store, None, None, clock=lambda: now, cooldown_seconds=0, max_followups=2)
            with store._connection() as db:
                db.execute("UPDATE incident_jobs SET status='completed',completed_at=?,automatic_followup=1", (now.isoformat(),))
                db.execute("INSERT INTO pending_investigation_followups VALUES (?,?,?,?,?,?)",
                           ("incident", "stream", "005", 1, '{}', "event"))
                worker._schedule_pending(db, now)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM incident_jobs").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM pending_investigation_followups").fetchone()[0], 1)

    def test_event_driven_focus_never_includes_a_later_window(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = EvidenceTools(EventStore(Path(directory) / "scope.db"), past_only_focus=True)
            self.assertEqual(tools._focus_indices({index: {} for index in range(6)}, 3), [1, 2, 3])

    def test_operator_request_is_available_after_automatic_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            run_id = store.create_run("payment_issue", 1, "poisson", {})
            DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            episode = '{"category":"payments","start_window":4,"latest_window":4}'
            with store._connection() as db:
                db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                           "VALUES (?,?,?,?,?,?,?,?)", ("incident", run_id, "payments", "ended", "004", "005", "event", episode))
                for index in range(2):
                    order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
                    db.execute("INSERT INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,episode_json,status,created_at,queue_order,automatic_followup) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,1)", (f"auto-{index}", "incident", run_id, "followup", "005", 0, episode, "completed", "2026-01-01T00:00:00Z", order))
            store.add_event(run_id, "005", "detector", "anomaly_alert",
                            {"category": "payments", "window_index": 4, "window_end": "005"})
            job = schedule_job(store, "incident", "Operator requested investigation", "manual-key")
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["automatic_followup"], 0)

    def test_delayed_manual_initial_investigation_uses_opening_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "queue.db")
            run_id = store.create_run("payment_issue", 1, "poisson", {})
            host = DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            for index, count in enumerate([4, 4, 4, 4, 40, 45]):
                observation = CountObservation(index, f"{index:03}", f"{index+1:03}", {"payments": count})
                result = host.process(observation)
                if result["categories"]["payments"]["alerted"]:
                    store.add_event(run_id, observation.window_end, "detector", "anomaly_alert",
                                    {"category": "payments", "window_index": index, "window_end": observation.window_end})
                host.deliver_pending(automatic=False)
            with store._connection() as db:
                incident_id = db.execute("SELECT incident_id FROM incidents").fetchone()[0]
            job = schedule_job(store, incident_id, "Operator requested investigation", "manual-first")
            self.assertEqual(job["cutoff"], "005")
            self.assertEqual(job["sequence_bound"], 1)
            self.assertEqual(json.loads(job["episode_json"])["latest_window"], 4)
            self.assertTrue(schedule_job(store, incident_id, "Operator requested investigation", "manual-double")["request_reused"])

    def test_scope_excludes_future_and_late_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "scope.db")
            run_id = store.create_run("healthy", 1, "poisson", {})
            before = store.add_event(run_id, "2026-01-01T10:00:00Z", "support_tickets", "ticket_created", {"body": "before"})
            future = store.add_event(run_id, "2026-01-01T12:00:00Z", "support_tickets", "ticket_created", {"body": "future"})
            scoped = ScopedStore(store, run_id, "2026-01-01T11:00:00Z", future["sequence"])
            late = store.add_event(run_id, "2026-01-01T09:00:00Z", "support_tickets", "ticket_created", {"body": "late import"})
            unavailable = store.add_event(run_id, "2026-01-01T09:00:00Z", "support_tickets", "ticket_created",
                                          {"body": "not yet available", "available_at": "2026-01-01T12:00:00Z"})
            self.assertEqual([item["evidence_id"] for item in scoped.search_events(run_id, ("support_tickets",))],
                             [before["evidence_id"]])
            wider = ScopedStore(store, run_id, "2026-01-01T11:00:00Z", unavailable["sequence"])
            self.assertNotIn(unavailable["evidence_id"], [item["evidence_id"] for item in wider.search_events(run_id, ("support_tickets",))])
            self.assertEqual([item["evidence_id"] for item in scoped.get_events_by_evidence_ids(
                run_id, [before["evidence_id"], future["evidence_id"], late["evidence_id"]])], [before["evidence_id"]])

    def test_job_freezes_only_other_open_category_context_available_at_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "cross-context.db")
            run_id = store.create_run("mixed_categories", 27, "poisson", {})
            DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4,
                                                               ["payments", "orders", "account"]))
            with store._connection() as db:
                for incident_id, category, status, start, end in (
                    ("payment-1", "payments", "active", 2, 2),
                    ("orders-1", "orders", "ended", 0, 1),
                    ("account-resolved", "account", "ended", 0, 0),
                ):
                    db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                               "VALUES (?,?,?,?,?,?,?,?)",
                               (incident_id, run_id, category, status, "2026-01-01T00:00:00Z",
                                "2026-01-01T01:00:00Z", incident_id,
                                json.dumps({"category": category, "start_window": start,
                                            "latest_window": end, "window_count": end-start+1})))
                db.execute("UPDATE incidents SET operational_status='resolved' WHERE incident_id='account-resolved'")
                db.execute("INSERT INTO episode_outbox (event_id,stream_id,episode_id,event_type,window_index,payload_json,created_at) "
                           "VALUES (?,?,?,?,?,?,?)",
                           ("open-payment", run_id, "payment-1", "episode_opened", 2,
                            json.dumps({"episode": {"category": "payments", "start_window": 2,
                                                     "latest_window": 2, "window_count": 1}}), "now"))
            for index, category, end, tickets in (
                (0, "orders", "2026-01-01T00:30:00Z", 5),
                (1, "orders", "2026-01-01T01:00:00Z", 6),
                (0, "account", "2026-01-01T00:30:00Z", 40),
                (2, "payments", "2026-01-01T01:30:00Z", 12),
            ):
                store.add_event(run_id, end, "detector", "anomaly_alert",
                                {"category": category, "window_index": index,
                                 "window_start": f"2026-01-01T{index:02d}:00:00Z",
                                 "window_end": end, "ticket_count": tickets, "expected": 4})
            job = schedule_job(store, "payment-1", "Operator requested investigation", "cross-context")
            snapshot = json.loads(job["cross_category_context_json"])
            self.assertEqual(len(snapshot), 1)
            self.assertEqual(snapshot[0]["incident_id"], "orders-1")
            self.assertEqual((snapshot[0]["alert_windows"], snapshot[0]["tickets"]), (2, 11))
            self.assertEqual(snapshot[0]["latest_window_end"], "2026-01-01T01:00:00Z")
            self.assertNotIn("payments", [item["category"] for item in snapshot])
            detail = IncidentWorkspace(store, "Local operator").detail("payment-1")
            self.assertEqual(detail["investigations"][0]["cross_category_context"], snapshot)

    def test_cross_category_evidence_search_respects_frozen_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "cross-evidence.db")
            run_id = store.create_run("mixed_categories", 27, "poisson", {})
            store.add_event(run_id, "2026-01-01T00:15:00Z", "product_logs", "service_log_summary",
                            {"service": "identity-service", "message": "verification latency elevated"})
            late = store.add_event(run_id, "2026-01-01T00:25:00Z", "product_logs", "service_log_summary",
                                   {"service": "identity-service", "message": "verification latency elevated",
                                    "available_at": "2026-01-01T01:30:00Z"})
            scoped = ScopedStore(store, run_id, "2026-01-01T01:00:00Z", late["sequence"])
            evidence = EvidenceTools(scoped)
            found = evidence.search_cross_category_records(
                run_id, "identity-service verification", "account",
                "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")
            self.assertEqual(found["count"], 1)
            self.assertNotEqual(found["records"][0]["evidence_id"], late["evidence_id"])

    def test_relationship_group_requires_per_incident_evidence_and_keeps_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "relationships.db")
            self._queue_fixture(store)
            context = [{"incident_id": "orders-1", "category": "orders"}]
            with store._connection() as db:
                db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                           "VALUES (?,?,?,?,?,?,?,?)",
                           ("orders-1", "stream", "orders", "active", "001", "002", "orders-event",
                            json.dumps({"category": "orders", "start_window": 1, "latest_window": 1})))
                db.execute("UPDATE incident_jobs SET status='running',fence_token=1,lease_until=? "
                           "WHERE job_id='first'", ("2026-01-01T00:01:00+00:00",))
                db.execute("UPDATE incident_jobs SET cross_category_context_json=? WHERE job_id='first'",
                           (json.dumps(context),))
            report = {"summary": "Possible shared checkout dependency.", "evidence": [
                {"evidence_id": "e-pay", "source": "product_logs", "observed_at": "001", "summary": "shared"},
                {"evidence_id": "e-orders", "source": "product_logs", "observed_at": "001", "summary": "shared"},
            ], "tool_trace": [
                {"tool": "search_cross_category_evidence", "arguments": {"incident_id": "incident"},
                 "result": {"records": [{"evidence_id": "e-pay", "payload": {"service": "checkout-orchestrator"}}]}},
                {"tool": "search_cross_category_evidence", "arguments": {"incident_id": "orders-1"},
                 "result": {"records": [{"evidence_id": "e-orders", "payload": {"service": "checkout-orchestrator"}}]}},
            ], "relationship_assessments": [{
                "title": "Checkout orchestration may connect both incidents",
                "shared_system": "checkout-orchestrator", "explanation": "Both incident intervals show errors in this service.",
                "status": "possible", "incident_ids": ["incident", "orders-1"],
                "supporting_evidence_by_incident": {"incident": ["e-pay"], "orders-1": ["e-orders"]},
            }]}
            worker = IncidentWorker(store, None, None, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertTrue(worker._commit_report({"job_id": "first", "fence_token": 1}, report))
            with store._connection() as db:
                group = db.execute("SELECT * FROM incident_relationship_groups").fetchone()
                self.assertEqual((group["status"], group["current_version"]), ("possible", 1))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM incident_relationship_members").fetchone()[0], 2)
            self.assertEqual(len(IncidentWorkspace(store).detail("incident")["relationships"]), 1)
            self.assertEqual(len(IncidentWorkspace(store).detail("orders-1")["relationships"]), 1)

            with store._connection() as db:
                db.execute("UPDATE incident_jobs SET status='running',fence_token=1,lease_until=? "
                           "WHERE job_id='second'", ("2026-01-01T00:01:00+00:00",))
                db.execute("UPDATE incident_jobs SET cross_category_context_json=? WHERE job_id='second'",
                           (json.dumps(context),))
            revised = json.loads(json.dumps(report).replace("e-pay", "e-pay-2").replace("e-orders", "e-orders-2"))
            revised["relationship_assessments"][0]["status"] = "weakened"
            self.assertTrue(worker._commit_report({"job_id": "second", "fence_token": 1}, revised))
            detail = IncidentWorkspace(store).detail("incident")
            self.assertEqual(detail["relationships"][0]["status"], "weakened")
            self.assertEqual(detail["relationships"][0]["current_version"], 2)
            self.assertEqual(len(detail["relationships"][0]["history"]), 2)

    def test_requested_comparison_freezes_scope_runs_durably_and_stays_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "comparison.db")
            run_id = store.create_run("mixed_categories", 2701, "poisson", {})
            DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments", "orders"]))
            interval = ("2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z")
            store.add_event(run_id, interval[1], "detector", "window_observation",
                            {"window_index": 0, "window_start": interval[0], "window_end": interval[1],
                             "category_counts": {"payments": 18, "orders": 21}})
            store.add_event(run_id, interval[1], "detector", "window_score",
                            {"window_index": 0, "scores_by_category": {
                                "payments": {"expected": 4.0}, "orders": {"expected": 4.0}}})
            for category, count in (("payments", 18), ("orders", 21)):
                episode = {"category": category, "start_window": 0, "latest_window": 0}
                incident_id = f"{category}-incident"
                with store._connection() as db:
                    db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                               "VALUES (?,?,?,?,?,?,?,?)",
                               (incident_id, run_id, category, "active", interval[1], interval[1], "event", json.dumps(episode)))
                store.add_event(run_id, interval[1], "detector", "anomaly_alert",
                                {"category": category, "window_index": 0, "window_start": interval[0],
                                 "window_end": interval[1], "ticket_count": count, "expected": 4.0,
                                 "scores": [{"score": 5.0, "threshold": 2.0, "anomalous": True}]})
                store.add_event(run_id, "2026-01-01T00:12:00Z", "support_tickets", "ticket_created",
                                {"category": category, "body": f"Synthetic {category} issue"})

            job = request_comparison(store, ["payments-incident", "orders-incident"], "compare-1")
            replay_later = store.add_event(run_id, "2026-01-01T01:00:00Z", "product_logs", "service_log_summary",
                                           {"service": "checkout-orchestrator", "message": "later"})
            reused = request_comparison(store, ["payments-incident", "orders-incident"], "compare-1")
            self.assertTrue(reused["request_reused"])
            self.assertEqual(reused["cutoff"], interval[1])
            self.assertLess(reused["sequence_bound"], replay_later["sequence"])

            class RecordingInvestigator:
                mode = "test"
                context = None

                def investigate(self, selected_run, question, evidence_tools, alert_context=None, on_progress=None):
                    self.context = alert_context
                    self.assert_cutoff = alert_context["primary_incident_context"]
                    assert [item["category"] for item in alert_context["selected_signals"]] == ["payments", "orders"]
                    records = evidence_tools.store.search_events(selected_run, ("support_tickets",), limit=100)
                    assert all(item["simulated_at"] <= interval[1] for item in records)
                    return RCAReport("completed", "Separate category findings compared.", [], [])

            investigator = RecordingInvestigator()
            worker = IncidentWorker(store, lambda: investigator, EvidenceTools(store))
            self.assertTrue(worker.run_one())
            completed = list_comparison_jobs(store, run_id)[0]
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["report"]["summary"], "Separate category findings compared.")
            self.assertEqual(len(completed["report"]["comparison_scope"]), 2)
            self.assertEqual(len(investigator.context["selected_signals"]), 2)
            with store._connection() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM incident_report_revisions").fetchone()[0], 0)

    def test_incident_and_comparison_jobs_share_fifo_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "shared-queue.db")
            self._queue_fixture(store)
            with store._connection() as db:
                order = db.execute("INSERT INTO investigation_queue_order DEFAULT VALUES").lastrowid
                db.execute("INSERT INTO incident_comparison_jobs "
                           "(job_id,stream_id,idempotency_key,selected_incident_ids_json,scope_json,cutoff,sequence_bound,status,created_at,queue_order,queued_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           ("comparison", "stream", "key", '["a","b"]', '[]', "002", 0,
                            "queued", "2026-01-01T00:00:00+00:00", order, "2026-01-01T00:00:00+00:00"))
            worker = IncidentWorker(store, None, None, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
            seen = []

            def complete(job):
                seen.append((job["job_kind"], job["job_id"]))
                table = "incident_jobs" if job["job_kind"] == "incident" else "incident_comparison_jobs"
                with store._connection() as db:
                    db.execute(f"UPDATE {table} SET status='completed',completed_at=? WHERE job_id=?",
                               ("2026-01-01T00:00:01+00:00", job["job_id"]))

            worker._execute = complete
            self.assertTrue(worker.run_one())
            self.assertTrue(worker.run_one())
            self.assertTrue(worker.run_one())
            self.assertEqual(seen, [("incident", "first"), ("incident", "second"), ("comparison", "comparison")])

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
                runtime.stop()
