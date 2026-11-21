import unittest

from agentic_ops.search_activity import summarize_search_trace


class SearchActivityTests(unittest.TestCase):
    def test_summarizes_source_counts_and_reference_passages(self):
        result = summarize_search_trace([{
            "tool": "search_evidence",
            "arguments": {"category": "payments", "query": "authorization timeout",
                          "sources": ["customer_tickets", "operational_docs"]},
            "result": {
                "available_through": "2026-01-01T00:00:00Z",
                "archive_index_ready": True,
                "results": {
                    "customer_tickets": {"tickets": [{"evidence_id": "T1"}], "count": 1},
                    "operational_docs": {"passages": [{
                        "evidence_id": "D1", "source": "knowledge_docs",
                        "available_at": "2025-12-01T00:00:00Z",
                        "retrieval": {"keyword_rank": 4, "vector_rank": 2,
                                      "reranker_rank": 1, "rrf_score": 0.03},
                        "payload": {"title": "Payment guide", "section_title": "Timeouts",
                                    "page_number": 3, "text": "Check gateway response codes.",
                                    "source_path": "data/knowledge_base/payments.md"},
                    }], "count": 1},
                },
            },
        }])

        self.assertEqual(len(result), 1)
        activity = result[0]
        self.assertEqual(activity["category"], "payments")
        self.assertEqual(activity["available_through"], "2026-01-01T00:00:00Z")
        self.assertTrue(activity["archive_index_ready"])
        self.assertEqual([source["count"] for source in activity["sources"]], [1, 1])
        document = activity["documents"][0]
        self.assertEqual(document["title"], "Payment guide")
        self.assertEqual(document["section_title"], "Timeouts")
        self.assertEqual(document["page_number"], 3)
        self.assertEqual(document["retrieval"]["reranker_rank"], 1)

    def test_marks_unavailable_source_and_empty_archive(self):
        result = summarize_search_trace([{
            "tool": "search_evidence",
            "arguments": {"category": "orders", "query": "queue backlog",
                          "sources": ["operational_docs"]},
            "result": {"archive_index_ready": False, "results": {},
                       "unavailable_sources": {"operational_docs": "Index missing"}},
        }])
        self.assertFalse(result[0]["archive_index_ready"])
        self.assertEqual(result[0]["sources"][0]["error"], "Index missing")
        self.assertEqual(result[0]["documents"], [])

    def test_supports_legacy_document_search_trace(self):
        result = summarize_search_trace([{
            "tool": "search_operational_docs",
            "arguments": {"category": "payments", "query": "gateway timeout"},
            "result": {"passages": [{"evidence_id": "D2", "source": "runbooks",
                                      "payload": {"title": "Payment runbook", "text": "Check retries."}}],
                       "count": 1},
        }])
        self.assertEqual(result[0]["sources"][0]["label"], "Reference library")
        self.assertEqual(result[0]["documents"][0]["source"], "runbooks")

    def test_ignores_non_search_and_malformed_trace_entries(self):
        self.assertEqual(summarize_search_trace([None, "bad", {"tool": "build_incident_timeline"}]), [])
        self.assertEqual(summarize_search_trace(None), [])


if __name__ == "__main__":
    unittest.main()
