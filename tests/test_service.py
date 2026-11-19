import tempfile
import time
import unittest
from pathlib import Path

from agentic_ops.agent import MockInvestigator
from agentic_ops.domain import SimulationConfig
from agentic_ops.service import OperationsRuntime
from agentic_ops.store import EventStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = OperationsRuntime(EventStore(Path(self.temp.name) / "runtime.sqlite3"), MockInvestigator())

    def tearDown(self):
        self.temp.cleanup()

    def test_run_emits_scores_alerts_and_keeps_truth_separate(self):
        config = SimulationConfig(seed=14, window_count=52, calibration_windows=20,
                                  tick_delay_seconds=0.0, baseline_tickets_per_window=3.0)
        run = self.runtime.start_run(config)
        result = self.runtime.wait_for_run(run["run_id"], timeout=20)
        self.assertEqual(result["status"], "completed")
        events = self.runtime.store.list_events(run["run_id"], limit=2000)
        self.assertTrue(any(event["event_type"] == "window_observation" for event in events))
        self.assertTrue(any(event["event_type"] == "window_score" for event in events))
        self.assertTrue(any(event["event_type"] == "anomaly_alert" for event in events))
        self.assertEqual(self.runtime.store.get_truth(run["run_id"])["cause"], "payment_authorization_failure")
        public_records = self.runtime.evidence.search_issues(run["run_id"])
        self.assertNotIn("payment_authorization_failure", repr(public_records))

    def test_operator_investigation_returns_cited_report(self):
        config = SimulationConfig(seed=4, window_count=52, calibration_windows=20,
                                  tick_delay_seconds=0.0, baseline_tickets_per_window=3.0)
        run = self.runtime.start_run(config)
        self.runtime.wait_for_run(run["run_id"], timeout=20)
        alert = self.runtime.store.search_events(run["run_id"], ("detector",), event_types=("anomaly_alert",))[-1]
        started = self.runtime.start_investigation(run["run_id"], "What is driving the increase?",
                                                   alert["evidence_id"])
        deadline = time.time() + 3
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
        for recommendation in report["recommendations"]:
            self.assertTrue(set(recommendation["supporting_evidence"]).issubset(evidence_ids))

    def test_investigation_rejects_duplicate_categories(self):
        config = SimulationConfig(seed=4, window_count=52, calibration_windows=20,
                                  tick_delay_seconds=0.0, baseline_tickets_per_window=3.0)
        run = self.runtime.start_run(config)
        self.runtime.wait_for_run(run["run_id"], timeout=20)
        alerts = self.runtime.store.search_events(run["run_id"], ("detector",), event_types=("anomaly_alert",))
        alert, earlier_alert = alerts[-1], alerts[-2]
        with self.assertRaisesRegex(ValueError, "one alert episode per category"):
            self.runtime.start_investigation(run["run_id"], "Compare these signals",
                                             alert_evidence_ids=[alert["evidence_id"], earlier_alert["evidence_id"]])


if __name__ == "__main__":
    unittest.main()
