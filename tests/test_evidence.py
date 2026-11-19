import tempfile
import unittest
from pathlib import Path

from agentic_ops.agent import MockInvestigator
from agentic_ops.evidence import EvidenceTools
from agentic_ops.store import EventStore


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name) / "test.sqlite3")
        self.run_id = self.store.create_run("payment_issue", 4, "poisson", {"calibration_windows": 4})
        self.store.add_truth(self.run_id, {"cause": "hidden_truth_value"})

    def tearDown(self):
        self.temp.cleanup()

    def test_read_tools_never_return_evaluator_truth(self):
        self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "support_tickets", "ticket_created",
                             {"ticket_key": "SUP-1", "title": "Payment failed", "category": "payments"})
        result = EvidenceTools(self.store).search_tickets(self.run_id, "Payment")
        self.assertNotIn("hidden_truth_value", repr(result))
        self.assertEqual(result["count"], 1)

    def test_mock_investigator_cites_tool_returned_evidence(self):
        self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "issue_tracker", "issue_created",
                             {"issue_key": "PAY-204", "title": "Payment authorization timeouts",
                              "status": "open", "service": "checkout-payments"})
        self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "product_logs", "service_log_summary",
                             {"service": "checkout-payments", "errors": 17, "requests": 20})
        self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "support_tickets", "ticket_created",
                             {"ticket_key": "SUP-1", "title": "Payment failed", "category": "payments"})
        self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "change_log", "change_record",
                             {"change_key": "CHG-1", "service": "checkout-payments"})
        tools = EvidenceTools(self.store)
        report = MockInvestigator().investigate(self.run_id, "What happened?", tools)
        cited = set(report.hypotheses[0].supporting_evidence)
        available = {item.evidence_id for item in report.evidence}
        self.assertEqual(report.status, "completed")
        self.assertTrue(cited)
        self.assertTrue(cited.issubset(available))


if __name__ == "__main__":
    unittest.main()
