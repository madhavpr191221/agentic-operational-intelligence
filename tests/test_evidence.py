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

    def _add_window(self, index, start, end, observed):
        self.store.add_event(self.run_id, end, "detector", "window_observation",
                             {"window_index": index, "window_start": start, "window_end": end,
                              "ticket_count": observed, "category_counts": {"payments": observed}})
        self.store.add_event(self.run_id, end, "detector", "window_score",
                             {"window_index": index,
                              "scores_by_category": {"payments": {"observed": observed, "expected": 2.0,
                                                                     "alerted": observed > 2, "scores": []}}})

    def test_analysis_tools_compare_segments_rank_text_and_order_timeline(self):
        self._add_window(0, "2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", 1)
        self._add_window(1, "2026-01-01T00:30:00Z", "2026-01-01T01:00:00Z", 2)
        self.store.add_event(self.run_id, "2026-01-01T00:10:00Z", "support_tickets", "ticket_created",
                             {"ticket_key": "SUP-OLD", "window_index": 0, "category": "payments",
                              "reason": "other", "channel": "email", "title": "Old question", "body": "Old"})
        self.store.add_event(self.run_id, "2026-01-01T00:45:00Z", "support_tickets", "ticket_created",
                             {"ticket_key": "SUP-NEW", "window_index": 1, "category": "payments",
                              "reason": "authorization_failure", "channel": "chat", "title": "Payment failed",
                              "body": "Authorization timed out at checkout."})
        self.store.add_event(self.run_id, "2026-01-01T00:20:00Z", "issue_tracker", "issue_created",
                             {"issue_key": "PAY-1", "title": "Payment timeouts", "status": "open",
                              "service": "checkout-payments"})
        self.store.add_event(self.run_id, "2026-01-01T00:50:00Z", "product_logs", "service_log_summary",
                             {"service": "checkout-payments", "requests": 10, "errors": 4,
                              "message": "Payment authorization timeout"})
        tools = EvidenceTools(self.store)
        comparison = tools.compare_categories(self.run_id, 1)
        contributors = tools.rank_ticket_contributors(self.run_id, "payments", 1)
        timeline = tools.build_incident_timeline(self.run_id, "payments", 1)
        before_after = tools.compare_before_after(self.run_id, "payments", 1)
        self.assertEqual(comparison["categories"][0]["observed"], 2)
        self.assertEqual(contributors["category_ticket_count"], 1)
        self.assertEqual(contributors["contributors"][0]["value"], "authorization_failure")
        stamps = [item["observed_at"] for item in timeline["events"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(before_after["before"]["ticket_count_total"], 1)
        self.assertEqual(before_after["before"]["ticket_count"], 1)
        self.assertTrue(before_after["before"]["missing_windows"])
        self.assertEqual(before_after["during"]["ticket_count"], 1)
        self.assertEqual(before_after["during"]["service"]["error_rate"], 0.4)

    def test_runbook_action_requires_open_issue_and_failure_log_evidence(self):
        issue = self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "issue_tracker", "issue_created",
                             {"issue_key": "PAY-1", "title": "Payment timeouts", "status": "open",
                              "service": "checkout-payments"})
        log = self.store.add_event(self.run_id, "2026-01-01T00:01:00Z", "product_logs", "service_log_summary",
                             {"service": "checkout-payments", "requests": 10, "errors": 3})
        result = EvidenceTools(self.store).suggest_runbook_actions(
            self.run_id, "payments", [issue["evidence_id"], log["evidence_id"]])
        self.assertEqual(result["actions"][0]["runbook_id"], "RB-PAY-01")
        self.assertEqual(set(result["actions"][0]["evidence_ids"]),
                         {issue["evidence_id"], log["evidence_id"]})
        self.assertNotIn("execute", result["actions"][0]["next_step"].casefold())


if __name__ == "__main__":
    unittest.main()
