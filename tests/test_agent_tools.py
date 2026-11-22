import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentic_ops.agent import InvestigationNotes, OpenAIInvestigator
from agentic_ops.domain import RCAReport
from agentic_ops.evidence import EvidenceTools
from agentic_ops.store import EventStore


class AgentToolContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_investigator_exposes_six_read_only_tools(self):
        expected = {
            "search_evidence",
            "query_service_health",
            "analyze_customer_tickets",
            "compare_incident_context",
            "build_incident_timeline",
            "get_runbook_guidance",
        }
        captured = {}

        def capture_agent(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(tools=kwargs["tools"])

        class FinishedStream:
            final_output = InvestigationNotes(summary="No cause established.", hypotheses=[],
                                              unresolved_questions=[])

            async def stream_events(self):
                if False:
                    yield None

        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "tools.sqlite3")
            tools = EvidenceTools(store)
            episode = {
                "start_window_index": 10,
                "end_window_index": 10,
                "focus_window_index": 10,
                "context_start_at": "2026-01-01T00:00:00Z",
                "analysis_end_at": "2026-01-01T04:00:00Z",
                "change_start_at": "2025-12-30T04:00:00Z",
                "focus": {"start_window_index": 8, "end_window_index": 11,
                          "start_at": "2026-01-01T03:00:00Z",
                          "end_at": "2026-01-01T05:00:00Z"},
            }
            context = {"selected_signals": [{"category": "payments", "window_index": 10,
                                               "episode_summary": episode}]}
            investigator = OpenAIInvestigator(model="test-model")

            with patch("agentic_ops.agent.Agent", side_effect=capture_agent), \
                    patch("agentic_ops.agent.Runner.run_streamed", return_value=FinishedStream()), \
                    patch.object(tools, "search_tickets", return_value={"tickets": [], "count": 0}) as search_tickets, \
                    patch.object(tools, "search_operational_docs", return_value={"passages": [], "count": 0}) as search_docs, \
                    patch.object(investigator, "_compose_report",
                                 new=AsyncMock(return_value=RCAReport("abstained", "No cause", [], []))):
                await investigator._investigate("run-id", "What changed?", tools, context, None)
                search_tool = next(tool for tool in captured["tools"] if tool.name == "search_evidence")
                context = SimpleNamespace(tool_name="search_evidence", _function_tool_arguments=None,
                                          run_config=None)
                output = await search_tool.on_invoke_tool(context, json.dumps({
                    "category": "payments", "query": "authorization timeout",
                    "sources": ["customer_tickets"], "limit": 5,
                }))
                parsed = json.loads(output)
                self.assertIn("operational_docs", parsed["results"])
                search_tickets.assert_called_once()
                search_docs.assert_called_once()
                for hours in (6, 24):
                    output = await search_tool.on_invoke_tool(context, json.dumps({
                        "category": "payments", "query": "authorization timeout",
                        "sources": ["customer_tickets"], "limit": 5,
                        "lookback_hours": hours, "expansion_reason": "The origin of timeouts remains unknown.",
                    }))
                    parsed = json.loads(output)
                    self.assertEqual(parsed["recent_start"], "2025-12-31T22:00:00Z" if hours == 6 else "2025-12-31T04:00:00Z")

        actual = {tool.name for tool in captured["tools"]}
        self.assertEqual(actual, expected)
        self.assertEqual(len(captured["tools"]), 6)
        self.assertEqual(investigator.MAX_TOOL_CALLS, 32)
        search_schema = next(tool.params_json_schema for tool in captured["tools"]
                             if tool.name == "search_evidence")
        self.assertIn("sources", search_schema["properties"])
        self.assertTrue(search_schema["properties"]["sources"].get("items"))


if __name__ == "__main__":
    unittest.main()
