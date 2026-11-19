"""Bounded OpenAI tool-using investigator and deterministic offline mode."""

from __future__ import annotations

import json
import os
from typing import Any

from agentic_ops.domain import EvidenceRef, Hypothesis, RCAReport
from agentic_ops.evidence import EvidenceTools

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "hypotheses": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "explanation": {"type": "string"},
            "rank": {"type": "integer", "minimum": 1, "maximum": 3},
            "supporting_evidence": {"type": "array", "items": {"type": "string"}},
            "contradicting_evidence": {"type": "array", "items": {"type": "string"}},
        }, "required": ["title", "explanation", "rank", "supporting_evidence", "contradicting_evidence"],
            "additionalProperties": False}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "hypotheses", "unresolved_questions"],
    "additionalProperties": False,
}


class Investigator:
    def investigate(self, run_id: str, question: str, evidence_tools: EvidenceTools,
                    alert_context: dict[str, Any] | None = None) -> RCAReport:
        raise NotImplementedError


class MockInvestigator(Investigator):
    """Evidence-driven offline demo. This is not an LLM result."""

    def investigate(self, run_id: str, question: str, evidence_tools: EvidenceTools,
                    alert_context: dict[str, Any] | None = None) -> RCAReport:
        tool_trace: list[dict[str, Any]] = []
        run = evidence_tools.store.get_run(run_id) or {}
        outputs = [
            ("run_anomaly_detection", evidence_tools.run_anomaly_detection(
                run_id, int(alert_context.get("window_index", -1)) if alert_context else -1,
                run.get("arrival_model", "poisson"))),
            ("search_issues", evidence_tools.search_issues(run_id, "Payment authorization", 10)),
            ("search_product_logs", evidence_tools.search_product_logs(run_id, "checkout-payments", 10)),
            ("search_tickets", evidence_tools.search_tickets(run_id, "Payment", 12)),
            ("list_changes", evidence_tools.list_changes(run_id, "checkout-payments", 10)),
        ]
        for name, result in outputs:
            tool_trace.append({"tool": name, "result": result})

        evidence_by_id: dict[str, EvidenceRef] = {}
        _collect_evidence_refs(outputs, evidence_by_id)
        issues = outputs[1][1].get("issues", [])
        logs = outputs[2][1].get("logs", [])
        tickets = outputs[3][1].get("tickets", [])
        changes = outputs[4][1].get("changes", [])
        latest_issue_by_key: dict[str, dict[str, Any]] = {}
        for item in issues:
            payload = item.get("payload", {})
            key = payload.get("issue_key")
            if key:
                latest_issue_by_key[key] = item
        open_payment = [item for item in latest_issue_by_key.values()
                        if item.get("payload", {}).get("status") == "open"
                        and item.get("payload", {}).get("service") == "checkout-payments"]
        failing_logs = [item for item in logs if item.get("payload", {}).get("errors", 0) > 0
                        and item.get("payload", {}).get("service") == "checkout-payments"]
        payment_tickets = [item for item in tickets if item.get("payload", {}).get("category") == "payments"]
        if open_payment and failing_logs and payment_tickets:
            support = [open_payment[-1]["evidence_id"], failing_logs[-1]["evidence_id"], payment_tickets[-1]["evidence_id"]]
            if changes:
                support.append(changes[-1]["evidence_id"])
            hypotheses = [Hypothesis(
                title="Unresolved payment authorization failure",
                explanation="The payment issue is open, payment-service errors are present, and recent support tickets describe payment failures. The evidence supports this as a plausible cause of the ticket increase.",
                rank=1, supporting_evidence=support,
            )]
            summary = "The available evidence points to an unresolved payment-service problem as a plausible explanation for the ticket increase."
            status = "completed"
        else:
            hypotheses = []
            summary = "I could not establish a cause from the available synthetic evidence."
            status = "abstained"
        return RCAReport(status=status, summary=summary, hypotheses=hypotheses,
                         evidence=list(evidence_by_id.values()),
                         unresolved_questions=[] if hypotheses else ["Which service error, if any, is driving the observed ticket pattern?"],
                         tool_trace=tool_trace)


class OpenAIInvestigator(Investigator):
    # Backstop for a single evidence-selection pass; hitting it yields a partial report.
    TOOL_LIMIT = 24

    def __init__(self, model: str | None = None, client: Any | None = None):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-6.1-sol")
        if client is None:
            from openai import OpenAI
            client = OpenAI()
        self.client = client

    def investigate(self, run_id: str, question: str, evidence_tools: EvidenceTools,
                    alert_context: dict[str, Any] | None = None) -> RCAReport:
        tool_functions = self._tool_functions(run_id, evidence_tools)
        trace: list[dict[str, Any]] = []
        raw_results: list[tuple[str, Any]] = []
        tools = self._tool_schemas()
        response = self.client.responses.create(
            model=self.model,
            store=False,
            instructions=("You investigate fictional operational incidents. Use tools for factual claims. "
                          "The simulator truth is unavailable; do not claim causality is proven. In this one "
                          "evidence-selection pass, request a concise set of relevant tools, usually 3 to 6 calls. "
                          "Avoid duplicate searches. The next stage will produce the report, so do not request "
                          "follow-up tool rounds."),
            input=json.dumps({"operator_question": question, "selected_alert": alert_context}),
            tools=tools,
        )
        if getattr(response, "status", "completed") != "completed":
            raise RuntimeError(f"Investigation response ended with status {response.status}.")
        calls = [item for item in response.output if item.type == "function_call"]
        budget_reached = len(calls) > self.TOOL_LIMIT
        for call in calls[:self.TOOL_LIMIT]:
            arguments = json.loads(call.arguments)
            handler = tool_functions.get(call.name)
            if handler is None:
                raise ValueError(f"Tool is not allowed: {call.name}")
            result = handler(**arguments)
            raw_results.append((call.name, result))
            trace.append({"tool": call.name, "arguments": arguments, "result": result})

        evidence_by_id: dict[str, EvidenceRef] = {}
        _collect_evidence_refs(raw_results, evidence_by_id)
        final_response = self.client.responses.create(
            model=self.model,
            store=False,
            instructions=("Produce a concise evidence-grounded RCA report. Rank plausible explanations. "
                          "Every factual hypothesis must cite evidence IDs found in the supplied tool results. "
                          "If evidence is insufficient, return no unsupported hypothesis and say what remains unknown. "
                          "The budget_reached field will be supplied. If it is true, make clear the investigation is partial."),
            input=[{"role": "user", "content": json.dumps({"question": question, "selected_alert": alert_context,
                                                               "tool_results": raw_results,
                                                               "budget_reached": budget_reached})}],
            text={"format": {"type": "json_schema", "name": "rca_report", "strict": True, "schema": REPORT_SCHEMA}},
        )
        content = json.loads(final_response.output_text)
        valid_ids = set(evidence_by_id)
        hypotheses: list[Hypothesis] = []
        for item in content.get("hypotheses", []):
            supporting = [e for e in item["supporting_evidence"] if e in valid_ids]
            contradicting = [e for e in item["contradicting_evidence"] if e in valid_ids]
            if not supporting:
                continue
            hypotheses.append(Hypothesis(item["title"], item["explanation"], item["rank"], supporting, contradicting))
        unresolved = list(content.get("unresolved_questions", []))
        status = "incomplete" if budget_reached else "completed" if hypotheses else "abstained"
        summary = content.get("summary", "Investigation completed.")
        if budget_reached:
            summary = "The tool-call ceiling was reached; this is a partial investigation. " + summary
        if content.get("hypotheses") and not hypotheses:
            summary = "The model did not return hypotheses supported by valid tool evidence. " + summary
            unresolved.append("Which evidence can independently confirm the proposed cause?")
        return RCAReport(status=status, summary=summary, hypotheses=hypotheses,
                         evidence=list(evidence_by_id.values()), unresolved_questions=unresolved, tool_trace=trace)

    @staticmethod
    def _tool_schemas() -> list[dict[str, Any]]:
        return [
            {"type": "function", "name": "get_detection_summary", "description": "Get alert and latest detector context for this run.",
             "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True},
            {"type": "function", "name": "run_anomaly_detection", "description": "Re-run count detectors for an available ticket window.",
             "parameters": {"type": "object", "properties": {"window_index": {"type": "integer"},
             "model": {"type": "string", "enum": ["poisson", "negative_binomial"]}},
             "required": ["window_index", "model"], "additionalProperties": False}, "strict": True},
            {"type": "function", "name": "search_tickets", "description": "Search synthetic support ticket records.",
             "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
             "required": ["query", "limit"], "additionalProperties": False}, "strict": True},
            {"type": "function", "name": "search_issues", "description": "Search fictional issue tracker records.",
             "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
             "required": ["query", "limit"], "additionalProperties": False}, "strict": True},
            {"type": "function", "name": "search_product_logs", "description": "Search synthetic product service logs.",
             "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
             "required": ["query", "limit"], "additionalProperties": False}, "strict": True},
            {"type": "function", "name": "list_changes", "description": "List synthetic changes, optionally by service name.",
             "parameters": {"type": "object", "properties": {"service": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
             "required": ["service", "limit"], "additionalProperties": False}, "strict": True},
        ]

    @staticmethod
    def _tool_functions(run_id: str, tools: EvidenceTools) -> dict[str, Any]:
        return {
            "get_detection_summary": lambda: tools.detection_summary(run_id),
            "run_anomaly_detection": lambda window_index, model: tools.run_anomaly_detection(run_id, window_index, model),
            "search_tickets": lambda query, limit: tools.search_tickets(run_id, query, limit),
            "search_issues": lambda query, limit: tools.search_issues(run_id, query, limit),
            "search_product_logs": lambda query, limit: tools.search_product_logs(run_id, query, limit),
            "list_changes": lambda service, limit: tools.list_changes(run_id, service, limit),
        }


def _collect_evidence_refs(value: Any, target: dict[str, EvidenceRef]) -> None:
    if isinstance(value, dict):
        evidence_id = value.get("evidence_id")
        if evidence_id and evidence_id not in target:
            source = value.get("source", "synthetic evidence")
            timestamp = value.get("observed_at", "unknown time")
            summary = value.get("summary") or value.get("event_type") or "Tool result"
            target[evidence_id] = EvidenceRef(evidence_id, source, timestamp, str(summary))
        for child in value.values():
            _collect_evidence_refs(child, target)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_evidence_refs(child, target)
