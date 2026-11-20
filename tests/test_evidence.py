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

    def test_investigator_evidence_tools_do_not_recompute_detection(self):
        self.assertFalse(hasattr(EvidenceTools, "run_anomaly_detection"))

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
        self._add_window(0, "2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", 1)
        self.store.add_event(self.run_id, "2026-01-01T00:30:00Z", "detector", "anomaly_alert",
                             {"category": "payments", "window_index": 0, "ticket_count": 1})
        tools = EvidenceTools(self.store)
        report = MockInvestigator().investigate(self.run_id, "What happened?", tools,
                                                {"category": "payments", "window_index": 0})
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
        self.assertEqual(before_after["before_minutes"], 30)
        self.assertFalse(before_after["before"]["missing_windows"])
        self.assertEqual(before_after["during"]["ticket_count"], 1)
        self.assertEqual(before_after["during"]["service"]["error_rate"], 0.4)

    def test_runbook_action_requires_open_issue_and_failure_log_evidence(self):
        issue = self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "issue_tracker", "issue_created",
                             {"issue_key": "PAY-1", "title": "Payment timeouts", "status": "open",
                              "service": "checkout-payments"})
        log = self.store.add_event(self.run_id, "2026-01-01T00:01:00Z", "product_logs", "service_log_summary",
                             {"service": "checkout-payments", "requests": 10, "errors": 3})
        runbook = self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "runbooks", "runbook_page",
                             {"runbook_id": "payments", "page_number": 1, "title": "Payment guide",
                              "text": "Compare timeout rate and response codes."})
        result = EvidenceTools(self.store).suggest_runbook_actions(
            self.run_id, "payments", [issue["evidence_id"], log["evidence_id"], runbook["evidence_id"]])
        self.assertEqual(result["actions"][0]["runbook_id"], "RB-PAY-01")
        self.assertEqual(set(result["actions"][0]["evidence_ids"]),
                         {issue["evidence_id"], log["evidence_id"], runbook["evidence_id"]})
        self.assertNotIn("execute", result["actions"][0]["next_step"].casefold())

    def test_issue_search_obeys_alert_as_of_cutoff(self):
        self.store.add_event(self.run_id, "2026-01-01T01:00:00Z", "issue_tracker", "issue_created",
                             {"issue_key": "PAY-OLD", "title": "Payment timeout", "status": "open"})
        self.store.add_event(self.run_id, "2026-01-01T02:00:00Z", "issue_tracker", "issue_created",
                             {"issue_key": "PAY-FUTURE", "title": "Payment timeout resolved", "status": "resolved"})
        self.store.add_event(self.run_id, "2026-01-01T01:30:00Z", "issue_tracker", "issue_created",
                             {"issue_key": "PAY-AT-CUTOFF", "title": "Payment timeout update", "status": "open"})
        result = EvidenceTools(self.store).search_issues(
            self.run_id, "payment timeout", end_at="2026-01-01T01:30:00Z")
        self.assertEqual([item["payload"]["issue_key"] for item in result["issues"]], ["PAY-OLD"])

    def test_operational_document_search_obeys_alert_cutoff_and_returns_locations(self):
        self.store.add_event(self.run_id, "2026-01-01T00:00:00Z", "knowledge_docs", "document_passage",
                             {"document_id": "service-guide", "document_type": "service_guide",
                              "title": "Service catalog", "section_title": "Payment dependencies",
                              "categories": ["payments"], "source_path": "data/knowledge_base/service_catalog.md",
                              "text": "Northstar Gateway authorization timeout and response codes."})
        self.store.add_event(self.run_id, "2026-01-01T02:00:00Z", "knowledge_docs", "document_passage",
                             {"document_id": "future-note", "document_type": "postmortem",
                              "title": "Future review", "section_title": "Payment incident",
                              "categories": ["payments"], "source_path": "data/knowledge_base/future.md",
                              "text": "Northstar Gateway authorization timeout and response codes."})
        result = EvidenceTools(self.store).search_operational_docs(
            self.run_id, "Northstar Gateway authorization timeout", "payments",
            "2026-01-01T01:00:00Z")
        self.assertEqual([item["payload"]["document_id"] for item in result["passages"]],
                         ["service-guide"])
        self.assertEqual(result["passages"][0]["section_title"], "Payment dependencies")

    def test_ticket_themes_compare_alert_with_equal_length_baseline(self):
        self._add_window(0, "2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", 1)
        self._add_window(1, "2026-01-01T00:30:00Z", "2026-01-01T01:00:00Z", 2)
        self.store.add_event(self.run_id, "2026-01-01T00:15:00Z", "support_tickets", "ticket_created",
                             {"category": "payments", "reason": "refund_request", "title": "Refund question"})
        self.store.add_event(self.run_id, "2026-01-01T00:45:00Z", "support_tickets", "ticket_created",
                             {"category": "payments", "reason": "payment_timeout", "title": "Timeout"})
        result = EvidenceTools(self.store).compare_ticket_themes(self.run_id, "payments", 1, 1)
        self.assertEqual(result["baseline_interval"]["ticket_count"], 1)
        self.assertEqual(result["episode_interval"]["ticket_count"], 1)
        self.assertEqual(result["themes"][0]["theme"], "payment timeout")


if __name__ == "__main__":
    unittest.main()
