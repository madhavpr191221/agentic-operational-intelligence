import tempfile
import unittest
from pathlib import Path

from agentic_ops.agent import MockInvestigator
from agentic_ops.domain import SimulationConfig
from agentic_ops.evidence import EvidenceTools
from agentic_ops.retrieval import load_knowledge_passages, load_runbook_pages
from agentic_ops.simulation import generate_run
from agentic_ops.store import EventStore


class ScenarioRcaTests(unittest.TestCase):
    def _investigate(self, scenario, category):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = EventStore(Path(temporary.name) / "scenario.sqlite3")
        run_id = store.create_run(scenario, 51, "poisson", {"calibration_windows": 8})
        start_at = "2026-01-01T00:00:00Z"
        for page in load_runbook_pages():
            store.add_event(run_id, start_at, "runbooks", "runbook_page", page)
        for passage in load_knowledge_passages():
            store.add_event(run_id, passage["available_at"], "knowledge_docs",
                            "document_passage", passage)
        config = SimulationConfig(scenario=scenario, seed=51, window_count=24,
                                  calibration_windows=8, tick_delay_seconds=0)
        for window in generate_run(config):
            for event in window.events:
                store.add_event(run_id, event.simulated_at, event.source, event.event_type, event.payload)
            store.add_event(run_id, window.end, "detector", "window_observation",
                            {"window_index": window.index, "window_start": window.start,
                             "window_end": window.end, "ticket_count": window.ticket_count,
                             "category_counts": window.category_counts})
            store.add_event(run_id, window.end, "detector", "window_score",
                            {"window_index": window.index, "scores_by_category": {
                                name: {"expected": 5.0} for name in window.category_counts}})
            if window.index >= 20:
                store.add_event(run_id, window.end, "detector", "anomaly_alert",
                                {"category": category, "window_index": window.index,
                                 "ticket_count": window.category_counts[category],
                                 "scores": [{"anomalous": True, "score": 10, "threshold": 1}]})
        tools = EvidenceTools(store)
        summary = tools.build_episode_summary(run_id, category, 20, 23, 23)
        report = MockInvestigator().investigate(
            run_id, "What changed?", tools,
            {"selected_signals": [{"category": category, "window_index": 23,
                                   "episode_summary": summary}]},
        )
        return report

    def test_incident_scenarios_support_category_specific_explanations(self):
        for scenario, category in (("payment_issue", "payments"),
                                   ("orders_queue_backlog", "orders"),
                                   ("delivery_carrier_degradation", "delivery")):
            with self.subTest(scenario=scenario):
                report = self._investigate(scenario, category)
                self.assertTrue(report.hypotheses)
                self.assertIn(category, report.hypotheses[0].categories)
                self.assertTrue(report.hypotheses[0].supporting_evidence)
                self.assertTrue(report.recommendations[0].runbook_id)

    def test_controls_do_not_create_payment_cause_claims(self):
        for scenario in ("healthy", "unrelated_issue", "resolved_issue"):
            with self.subTest(scenario=scenario):
                report = self._investigate(scenario, "payments")
                self.assertFalse(report.hypotheses)
                self.assertEqual(report.status, "abstained")


if __name__ == "__main__":
    unittest.main()
