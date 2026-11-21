import tempfile
import time
import unittest
from pathlib import Path

from agentic_ops.agent import MockInvestigator
from agentic_ops.domain import RCAReport, SimulationConfig
from agentic_ops.detector_host import list_incidents
from agentic_ops.service import OperationsRuntime
from agentic_ops.store import EventStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = OperationsRuntime(EventStore(Path(self.temp.name) / "runtime.sqlite3"), MockInvestigator())

    def tearDown(self):
        self.runtime.incident_worker.stop()
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
