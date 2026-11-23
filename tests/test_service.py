import tempfile
import time
import unittest
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agentic_ops.agent import MockInvestigator
from agentic_ops.domain import RCAReport, SimulationConfig
from agentic_ops.detector_host import list_incidents
from agentic_ops.service import OperationsRuntime
from agentic_ops.service import RunControl
from agentic_ops.simulation import GeneratedEvent, GeneratedWindow
from agentic_ops.store import EventStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = OperationsRuntime(EventStore(Path(self.temp.name) / "runtime.sqlite3"), MockInvestigator())

    def tearDown(self):
        self.runtime.stop()
        self.temp.cleanup()

    def test_run_emits_scores_alerts_and_keeps_truth_separate(self):
        config = SimulationConfig(seed=14, window_count=52, calibration_windows=20,
                                  tick_delay_seconds=0.0)
        run = self.runtime.start_run(config)
        result = self.runtime.wait_for_run(run["run_id"], timeout=20)
        self.assertEqual(result["status"], "completed")
        events = self.runtime.store.list_events(run["run_id"], limit=2000)
        self.assertTrue(any(event["source"] == "knowledge_docs"
                            and event["event_type"] == "document_passage" for event in events))
        self.assertTrue(any(event["event_type"] == "window_observation" for event in events))
        self.assertTrue(any(event["event_type"] == "window_score" for event in events))
        self.assertTrue(any(event["event_type"] == "anomaly_alert" for event in events))
        incidents = list_incidents(self.runtime.store, run["run_id"])
        self.assertTrue(any(incident["category"] == "payments" for incident in incidents))
        observation = next(event for event in events if event["event_type"] == "window_observation")
        score_event = next(event for event in events if event["event_type"] == "window_score")
        self.assertEqual(observation["payload"]["schema_version"], 1)
        self.assertEqual(score_event["payload"]["schema_version"], 1)
        self.assertIn("detection_result", score_event["payload"])
        self.assertNotIn("ticket_text", score_event["payload"]["detection_result"])
        alert = next(event for event in events if event["event_type"] == "anomaly_alert")
        self.assertEqual(alert["payload"]["statistical_episode"]["window_count"], 1)
        self.assertEqual(self.runtime.store.get_truth(run["run_id"])["cause"], "payment_authorization_failure")
        public_records = self.runtime.evidence.search_issues(run["run_id"])
        self.assertNotIn("payment_authorization_failure", repr(public_records))

    def test_live_replay_emits_warmed_baseline_snapshot(self):
        counts = {"payments": 5, "orders": 8, "account": 4, "delivery": 6}
        windows = [
            GeneratedWindow(0, "2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", 23, counts, []),
            GeneratedWindow(1, "2026-01-01T00:30:00Z", "2026-01-01T01:00:00Z", 0,
                            {category: 0 for category in counts}, []),
        ]
        config = SimulationConfig(window_count=2, calibration_windows=1,
                                  monitor_history_windows=1, playback_duration_seconds=0.001)
        run = self.runtime.store.create_run("healthy", 42, "poisson", {})
        with patch("agentic_ops.service.generate_live_week", return_value=windows):
            from agentic_ops.detection import CategoryDetectorSuite
            from agentic_ops.detector_host import DetectorHost
            host = DetectorHost(self.runtime.store, run,
                                CategoryDetectorSuite("poisson", 1, ("payments", "orders", "account", "delivery")))
            self.runtime._run_live_week(run, config, RunControl(), host, automatic=False)
        snapshot = next(event for event in self.runtime.store.list_events(run, limit=100)
                        if event["event_type"] == "detector_baseline_ready")
        self.assertTrue(snapshot["payload"]["baseline_ready"])
        self.assertEqual(snapshot["payload"]["categories"]["payments"]["expected_count"], 5)
        self.assertEqual(self.runtime.store.get_run(run)["status"], "completed")

        # The next seeded replay should hydrate the four-day-equivalent prefix
        # from the persistent snapshot and generate only its streamed suffix.
        second_run = self.runtime.store.create_run("healthy", 42, "poisson", {})
        second_host = DetectorHost(
            self.runtime.store, second_run,
            CategoryDetectorSuite("poisson", 1, ("payments", "orders", "account", "delivery")),
        )
        with patch("agentic_ops.service.generate_live_week", side_effect=AssertionError(
                "cached replay should not regenerate the history prefix")), \
             patch("agentic_ops.service.generate_live_stream", return_value=[]):
            self.runtime._run_live_week(second_run, config, RunControl(), second_host, automatic=False)
        reused = next(event for event in self.runtime.store.list_events(second_run, limit=100)
                      if event["event_type"] == "detector_baseline_ready")
        self.assertEqual(reused["payload"]["baseline_cache"], "reused")
        self.assertEqual(self.runtime.store.get_run(second_run)["status"], "completed")

    def test_new_runtime_stops_interrupted_non_resumable_run(self):
        run_id = self.runtime.store.create_run("healthy", 42, "poisson", {"investigation_mode": "manual"})
        self.runtime.store.set_run_status(run_id, "running")
        restarted_runtime = OperationsRuntime(self.runtime.store, MockInvestigator())
        try:
            recovered = self.runtime.store.get_run(run_id)
            self.assertEqual(recovered["status"], "stopped")
            self.assertIn("application restart", recovered["error"])
        finally:
            restarted_runtime.stop()

    def test_live_replay_resumes_from_event_checkpoint_without_duplicate_ticket(self):
        config = SimulationConfig(scenario="healthy", seed=42, window_count=3, calibration_windows=1,
                                  monitor_history_windows=1, playback_duration_seconds=0.1)
        history = GeneratedWindow(0, "2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", 23,
                                 {"payments": 5, "orders": 8, "account": 4, "delivery": 6}, [])
        first = GeneratedEvent("2026-01-01T00:31:00Z", "support_tickets", "ticket_created",
                               {"category": "payments", "title": "First released ticket"})
        second = GeneratedEvent("2026-01-01T00:32:00Z", "support_tickets", "ticket_created",
                                {"category": "payments", "title": "Next ticket"})
        windows = [
            history,
            GeneratedWindow(1, "2026-01-01T00:30:00Z", "2026-01-01T01:00:00Z", 1,
                            {"payments": 1, "orders": 0, "account": 0, "delivery": 0}, [first]),
            GeneratedWindow(2, "2026-01-01T01:00:00Z", "2026-01-01T01:30:00Z", 1,
                            {"payments": 1, "orders": 0, "account": 0, "delivery": 0}, [second]),
        ]
        run_id = self.runtime.store.create_run("healthy", 42, "poisson",
                                               {**asdict(config), "investigation_mode": "manual"})
        self.runtime.store.set_run_status(run_id, "running")
        released_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"replay:{run_id}:window:1:event:0"))
        self.runtime.store.record_replay_event(
            run_id, first.simulated_at, first.source, first.event_type,
            {**first.payload, "replay_phase": "stream", "window_index": 1}, released_id,
            {"window_index": 1, "event_index": 1,
             "current_window_counts": {"payments": 1, "orders": 0, "account": 0, "delivery": 0},
             "simulated_at": first.simulated_at, "completed_windows": 0},
        )
        with patch("agentic_ops.service.generate_live_week", return_value=windows), \
             patch("agentic_ops.service.generate_live_stream", return_value=windows[1:]):
            restarted_runtime = OperationsRuntime(self.runtime.store, MockInvestigator())
            try:
                recovered = self.runtime.store.get_run(run_id)
                self.assertEqual(recovered["status"], "paused")
                self.assertTrue(recovered["recovered_after_restart"])
                restarted_runtime.control_run(run_id, "resume")
                result = restarted_runtime.wait_for_run(run_id, timeout=5)
                self.assertEqual(result["status"], "completed", result.get("error"))
                tickets = [event for event in self.runtime.store.list_events(run_id, limit=500)
                           if event["event_type"] == "ticket_created"]
                self.assertEqual(len(tickets), 2)
                self.assertEqual(len({event["evidence_id"] for event in tickets}), 2)
                observations = [event for event in self.runtime.store.list_events(run_id, limit=500)
                                if event["event_type"] == "window_observation"]
                self.assertEqual([event["payload"]["window_index"] for event in observations], [0, 1, 2])
            finally:
                restarted_runtime.stop()

    def test_reprocessing_completed_live_window_does_not_duplicate_detector_events(self):
        config = SimulationConfig(scenario="payment_issue", seed=42, window_count=5,
                                  calibration_windows=4)
        run_id = self.runtime.store.create_run("payment_issue", 42, "poisson", asdict(config))
        from agentic_ops.detection import CategoryDetectorSuite
        from agentic_ops.detector_host import DetectorHost
        host = DetectorHost(self.runtime.store, run_id,
                            CategoryDetectorSuite("poisson", 4, ("payments", "orders", "account", "delivery")))
        for index in range(5):
            counts = {"payments": 40 if index == 4 else 4,
                      "orders": 8, "account": 4, "delivery": 6}
            start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index * 30)
            end = start + timedelta(minutes=30)
            window = GeneratedWindow(index, start.isoformat().replace("+00:00", "Z"),
                                     end.isoformat().replace("+00:00", "Z"), sum(counts.values()), counts, [])
            self.runtime._process_window(run_id, config, window, host, automatic=False)
            if index == 4:
                self.runtime._process_window(run_id, config, window, host, automatic=False)
        events = self.runtime.store.list_events(run_id, limit=500)
        for event_type in ("window_observation", "window_score", "anomaly_alert"):
            matching = [event for event in events if event["event_type"] == event_type
                        and event["payload"].get("window_index") == 4]
            self.assertEqual(len(matching), 1, event_type)
        self.assertEqual(len(list_incidents(self.runtime.store, run_id)), 1)

    def test_operator_investigation_returns_cited_report(self):
        config = SimulationConfig(seed=4, window_count=52, calibration_windows=20,
                                  tick_delay_seconds=0.0)
        run = self.runtime.start_run(config)
        self.runtime.wait_for_run(run["run_id"], timeout=20)
        alert = self.runtime.store.search_events(run["run_id"], ("detector",), event_types=("anomaly_alert",))[-1]
        started = self.runtime.start_investigation(run["run_id"], "What is driving the increase?",
                                                   alert["evidence_id"])
        deadline = time.time() + 10
        result = self.runtime.store.get_investigation(started["investigation_id"])
        while result and result["status"] in {"queued", "running"} and time.time() < deadline:
            time.sleep(0.02)
            result = self.runtime.store.get_investigation(started["investigation_id"])
        self.assertEqual(result["status"], "completed")
        report = result["report"]
        evidence_ids = {item["evidence_id"] for item in report["evidence"]}
        cited = set(report["hypotheses"][0]["supporting_evidence"])
        self.assertTrue(cited)
        self.assertTrue(cited.issubset(evidence_ids))
        self.assertEqual([finding["category"] for finding in report["category_findings"]], ["payments"])
        ticket_evidence = [item for item in report["evidence"] if item["source"] == "support_tickets"]
        self.assertTrue(any(item["summary"] != "ticket_created" for item in ticket_evidence))
        self.assertTrue(report["recommendations"])
        self.assertTrue(report["search_activity"])
        for recommendation in report["recommendations"]:
            self.assertTrue(set(recommendation["supporting_evidence"]).issubset(evidence_ids))

    def test_investigation_rejects_duplicate_categories(self):
        config = SimulationConfig(seed=4, window_count=52, calibration_windows=20,
                                  tick_delay_seconds=0.0)
        run = self.runtime.start_run(config)
        self.runtime.wait_for_run(run["run_id"], timeout=20)
        alerts = [event for event in self.runtime.store.search_events(
            run["run_id"], ("detector",), event_types=("anomaly_alert",)
        ) if event["payload"].get("category") == "payments"]
        alert, earlier_alert = alerts[-1], alerts[-2]
        with self.assertRaisesRegex(ValueError, "one alert episode per category"):
            self.runtime.start_investigation(run["run_id"], "Compare these signals",
                                             alert_evidence_ids=[alert["evidence_id"], earlier_alert["evidence_id"]])

    def test_completed_report_keeps_scope_when_investigator_omits_it(self):
        """Scope belongs to orchestration, including provider early-return paths."""
        class InvestigatorWithoutScope:
            def investigate(self, *args, **kwargs):
                return RCAReport("abstained", "Insufficient evidence", [], [])

        self.runtime._provided_investigator = InvestigatorWithoutScope()
        run = self.runtime.start_run(SimulationConfig(
            scenario="healthy", seed=42, window_count=52, calibration_windows=48,
            tick_delay_seconds=0.0))
        self.runtime.wait_for_run(run["run_id"], timeout=20)
        observations = self.runtime.store.search_events(
            run["run_id"], ("detector",), limit=1000, event_types=("window_observation",))
        windows = sorted(int(event["payload"]["window_index"]) for event in observations)
        start, end = windows[-4], windows[-2]
        for index in windows[-4:-1]:
            observation = next(event for event in observations
                               if int(event["payload"]["window_index"]) == index)
            self.runtime.store.add_event(
                run["run_id"], observation["simulated_at"], "detector", "anomaly_alert",
                {"category": "payments", "window_index": index,
                 "ticket_count": observation["payload"]["category_counts"]["payments"],
                 "expected": 4.5},
            )
        started = self.runtime.start_investigation(
            run["run_id"], "Investigate the selected episode", episode_selections=[{
                "category": "payments", "start_window_index": start,
                "end_window_index": end, "focus_window_index": end,
            }])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = self.runtime.store.get_investigation(started["investigation_id"])
            if saved["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        self.assertEqual(saved["status"], "abstained")
        summary = saved["report"]["episode_summaries"][0]
        windows = summary["focus"]["window_series"]
        available_indices = {int(event["payload"]["window_index"]) for event in observations}
        expected_indices = [index for index in range(end - 2, end + 2) if index in available_indices]
        self.assertEqual([w["window_index"] for w in windows], expected_indices)
        self.assertTrue(all(w["start_at"] and w["end_at"] for w in windows))
        self.assertEqual(summary["focus"]["total_tickets"], sum(w["tickets"] for w in windows))


if __name__ == "__main__":
    unittest.main()
