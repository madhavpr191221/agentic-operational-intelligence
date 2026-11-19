"""Read-only, evidence-grounded RCA through one OpenAI Agents SDK agent."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from collections.abc import Callable
from typing import Any, Literal

from agents import Agent, Runner, function_tool
from agents.exceptions import MaxTurnsExceeded
from pydantic import BaseModel, ConfigDict, Field, create_model

from agentic_ops.domain import EvidenceRef, Hypothesis, RCAReport
from agentic_ops.evidence import EvidenceTools

ProgressCallback = Callable[[dict[str, Any]], Any] | None
_agent_loop: asyncio.AbstractEventLoop | None = None
_agent_loop_lock = threading.Lock()


def _persistent_agent_loop() -> asyncio.AbstractEventLoop:
    """Keep the Agents SDK async client on one loop across investigation jobs."""
    global _agent_loop
    with _agent_loop_lock:
        if _agent_loop is not None and _agent_loop.is_running():
            return _agent_loop

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run_loop() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(target=run_loop, daemon=True, name="agent-async-loop")
        thread.start()
        ready.wait()
        _agent_loop = loop
        return loop


def _run_on_agent_loop(coroutine: Any) -> Any:
    loop = _persistent_agent_loop()
    return asyncio.run_coroutine_threadsafe(coroutine, loop).result()


class HypothesisNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    explanation: str
    rank: int = Field(ge=1, le=3)


class InvestigationNotes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    hypotheses: list[HypothesisNote]
    unresolved_questions: list[str]


class Investigator:
    def investigate(self, run_id: str, question: str, evidence_tools: EvidenceTools,
                    alert_context: dict[str, Any] | None = None,
                    on_progress: ProgressCallback = None) -> RCAReport:
        raise NotImplementedError


class MockInvestigator(Investigator):
    """Deterministic evidence-driven demo; it does not call an LLM."""

    def investigate(self, run_id: str, question: str, evidence_tools: EvidenceTools,
                    alert_context: dict[str, Any] | None = None,
                    on_progress: ProgressCallback = None) -> RCAReport:
        category = str((alert_context or {}).get("category", "payments"))
        query = {"payments": "payment", "orders": "order", "account": "account",
                 "delivery": "delivery"}.get(category, category)
        service = "checkout-payments" if category == "payments" else "catalog-search"
        searches = [
            ("search_tickets", {"query": query, "limit": 12},
             lambda: evidence_tools.search_tickets(run_id, query, 12)),
            ("search_issues", {"query": query, "limit": 10},
             lambda: evidence_tools.search_issues(run_id, query, 10)),
            ("search_product_logs", {"query": service, "limit": 10},
             lambda: evidence_tools.search_product_logs(run_id, service, 10)),
            ("list_changes", {"service": service, "limit": 10},
             lambda: evidence_tools.list_changes(run_id, service, 10)),
        ]
        outputs: list[tuple[str, Any]] = []
        trace: list[dict[str, Any]] = []
        for name, arguments, invoke in searches:
            if on_progress:
                on_progress({"type": "tool_started", "stage": "gathering_evidence",
                             "tool": name, "message": f"Searching {name.replace('_', ' ')}."})
            result = invoke()
            outputs.append((name, result))
            trace.append({"tool": name, "arguments": arguments, "result": result})
            if on_progress:
                on_progress({"type": "tool_completed", "stage": "gathering_evidence",
                             "tool": name, "message": _tool_summary(result)})

        evidence_by_id: dict[str, EvidenceRef] = {}
        _collect_evidence_refs(outputs, evidence_by_id)
        tickets = outputs[0][1].get("tickets", [])
        issues = outputs[1][1].get("issues", [])
        logs = outputs[2][1].get("logs", [])
        changes = outputs[3][1].get("changes", [])
        active_issue = next((item for item in reversed(issues)
                             if item.get("payload", {}).get("status") == "open"), None)
        failing_log = next((item for item in reversed(logs)
                            if item.get("payload", {}).get("errors", 0) > 0), None)
        matching_tickets = [item for item in tickets
                            if item.get("payload", {}).get("category") == category]

        if category == "payments" and active_issue and failing_log and matching_tickets:
            support = [active_issue["evidence_id"], failing_log["evidence_id"],
                       matching_tickets[-1]["evidence_id"]]
            if changes:
                support.append(changes[-1]["evidence_id"])
            hypotheses = [Hypothesis(
                title="Unresolved payment authorization failure",
                explanation="An open payment issue, payment-service errors, and payment-related tickets overlap in time. The evidence supports this as a plausible explanation for the category spike.",
                rank=1, supporting_evidence=support,
            )]
            summary = "The available evidence supports an unresolved payment-service problem as a plausible explanation for the payment-ticket increase."
            status = "completed"
            unresolved = []
        else:
            hypotheses = []
            summary = f"I could not establish a cause for the {category} ticket increase from the available synthetic evidence."
            status = "abstained"
            unresolved = [f"Which system event, if any, explains the {category} ticket increase?"]
        if on_progress:
            on_progress({"type": "report_ready", "stage": "report_ready",
                         "message": "Evidence-linked report is ready."})
        return RCAReport(status=status, summary=summary, hypotheses=hypotheses,
                         evidence=list(evidence_by_id.values()), unresolved_questions=unresolved,
                         tool_trace=trace)


class OpenAIInvestigator(Investigator):
    """One adaptive tool-using agent with application-level safety ceilings."""

    MAX_TURNS = 12
    MAX_TOOL_CALLS = 24
    MAX_SEARCHES_PER_SOURCE = 3

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-6.1-sol")

    def investigate(self, run_id: str, question: str, evidence_tools: EvidenceTools,
                    alert_context: dict[str, Any] | None = None,
                    on_progress: ProgressCallback = None) -> RCAReport:
        return _run_on_agent_loop(
            self._investigate(run_id, question, evidence_tools, alert_context, on_progress)
        )

    async def _investigate(self, run_id: str, question: str, evidence_tools: EvidenceTools,
                           alert_context: dict[str, Any] | None,
                           on_progress: ProgressCallback) -> RCAReport:
        trace: list[dict[str, Any]] = []
        evidence_by_id: dict[str, EvidenceRef] = {}
        evidence_records: dict[str, dict[str, Any]] = {}
        lock = threading.Lock()
        budget_reached = False
        calls_started = 0
        source_searches: dict[str, int] = {}
        cached_results: dict[str, str] = {}
        cached_raw_results: dict[str, dict[str, Any]] = {}
        aliases_by_evidence: dict[str, str] = {}
        evidence_by_alias: dict[str, str] = {}

        def run_tool(name: str, arguments: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> str:
            nonlocal budget_reached, calls_started
            cache_key = f"{name}:{json.dumps(arguments, sort_keys=True)}"
            with lock:
                if cache_key in cached_results:
                    cached = cached_results[cache_key]
                    trace.append({"tool": name, "arguments": arguments, "cached": True,
                                  "result": cached_raw_results[cache_key]})
                    if on_progress:
                        on_progress({"type": "tool_reused", "stage": "gathering_evidence", "tool": name,
                                     "message": "Reused the result of an identical earlier search."})
                    return cached
                if source_searches.get(name, 0) >= self.MAX_SEARCHES_PER_SOURCE:
                    blocked = {"search_limit": True,
                               "message": "This evidence source has reached its focused-search limit. Use evidence already gathered or investigate another source."}
                    trace.append({"tool": name, "arguments": arguments, "result": blocked})
                    if on_progress:
                        on_progress({"type": "source_limit", "stage": "gathering_evidence", "tool": name,
                                     "message": "Focused-search limit reached for this source."})
                    return json.dumps(blocked)
                if calls_started >= self.MAX_TOOL_CALLS:
                    denied = {"error": "Investigation safety ceiling reached; no more evidence searches were run."}
                    if not budget_reached:
                        budget_reached = True
                        trace.append({"tool": name, "arguments": arguments, "result": denied})
                        if on_progress:
                            on_progress({"type": "safety_ceiling", "stage": "partial",
                                         "tool": name, "message": "Search ceiling reached; preparing a partial report."})
                    return json.dumps(denied)
                calls_started += 1
                source_searches[name] = source_searches.get(name, 0) + 1
                if on_progress:
                    on_progress({"type": "tool_started", "stage": "gathering_evidence", "tool": name,
                                 "message": f"Searching {name.replace('_', ' ')}."})
            try:
                result = operation()
            except Exception as exc:
                result = {"error": str(exc)}
            with lock:
                trace.append({"tool": name, "arguments": arguments, "result": result})
                _collect_evidence_refs(result, evidence_by_id)
                _collect_evidence_records(result, evidence_records)
                model_result = _with_citation_aliases(result, aliases_by_evidence, evidence_by_alias)
                cached_results[cache_key] = json.dumps(model_result, ensure_ascii=False)
                cached_raw_results[cache_key] = result
            if on_progress:
                on_progress({"type": "tool_completed", "stage": "gathering_evidence", "tool": name,
                             "message": _tool_summary(result)})
            return cached_results[cache_key]

        @function_tool(name_override="search_tickets", description_override=
                       "Search synthetic support-ticket payloads using one or two category keywords. Do not put dates in the query; records include timestamps.")
        def search_tickets(query: str, limit: int = 5) -> str:
            """Search payload text using one or two keywords; timestamps are returned separately. Limit is clamped to 1 through 8."""
            limit = max(1, min(8, limit))
            return run_tool("search_tickets", {"query": query, "limit": limit},
                            lambda: evidence_tools.search_tickets(run_id, query, limit))

        @function_tool(name_override="search_issues", description_override=
                       "Search fictional issue records using one or two service or symptom keywords. Do not put dates in the query; records include timestamps.")
        def search_issues(query: str, limit: int = 5) -> str:
            """Search issue payload text with one or two keywords; timestamps are returned separately. Limit is clamped to 1 through 8."""
            limit = max(1, min(8, limit))
            return run_tool("search_issues", {"query": query, "limit": limit},
                            lambda: evidence_tools.search_issues(run_id, query, limit))

        @function_tool(name_override="search_product_logs", description_override=
                       "Search synthetic product logs using one or two service or error keywords. Do not put dates in the query; records include timestamps.")
        def search_product_logs(query: str, limit: int = 5) -> str:
            """Search log payload text with one or two keywords; timestamps are returned separately. Limit is clamped to 1 through 8."""
            limit = max(1, min(8, limit))
            return run_tool("search_product_logs", {"query": query, "limit": limit},
                            lambda: evidence_tools.search_product_logs(run_id, query, limit))

        @function_tool(name_override="list_changes", description_override=
                       "List synthetic changes for a service when change history could explain the signal.")
        def list_changes(service: str = "", limit: int = 5) -> str:
            """List fictional service changes. Limit is clamped to 1 through 8."""
            limit = max(1, min(8, limit))
            return run_tool("list_changes", {"service": service, "limit": limit},
                            lambda: evidence_tools.list_changes(run_id, service, limit))

        agent = Agent(
            name="Operations RCA Investigator",
            model=self.model,
            instructions=(
                "Investigate the selected synthetic operational signal using only the read-only evidence tools. "
                "The signal already contains the detector outputs; do not recompute statistics. Interpret Z-score, "
                "CUSUM, and log-likelihood ratio as distinct detector evidence, not values to add together. "
                "Start with focused searches across the most relevant sources. After seeing results, make a targeted "
                "follow-up only to resolve a named uncertainty. Do not repeat broad searches; each source allows at "
                "most three focused searches and identical queries reuse cached results. Stop once the evidence is "
                "sufficient to support or reject a cause. Search queries match record payload text: use one or two "
                "keywords (for example, 'payment' or 'checkout-payments') and never put timestamps in the query; "
                "timestamps are returned as record metadata. Return brief candidate explanations and uncertainties; "
                "the final report step will verify each claim against evidence and constrain its citations. "
                "Do not claim causality is proven or infer hidden scenario truth. "
                "Synthetic scenario truth is hidden and must not be inferred from scenario names."
            ),
            tools=[search_tickets, search_issues, search_product_logs, list_changes],
            output_type=InvestigationNotes,
        )
        if on_progress:
            on_progress({"type": "status", "stage": "choosing_evidence",
                         "message": "Reviewing detector scores and selecting evidence searches."})

        run_input = json.dumps({"operator_question": question, "selected_signal": alert_context}, ensure_ascii=False)
        try:
            streaming = Runner.run_streamed(agent, input=run_input, max_turns=self.MAX_TURNS)
            async for event in streaming.stream_events():
                # Tool progress is persisted by each tool wrapper. Raw model text is intentionally not exposed.
                if event.type not in {"raw_response_event", "agent_updated_stream_event", "run_item_stream_event"}:
                    continue
            notes = streaming.final_output
            if not isinstance(notes, InvestigationNotes):
                raise ValueError("The investigator did not return structured investigation notes.")
            return await self._compose_report(question, alert_context, notes, evidence_by_id,
                                              evidence_records, evidence_by_alias, trace,
                                              budget_reached, on_progress)
        except MaxTurnsExceeded:
            if on_progress:
                on_progress({"type": "safety_ceiling", "stage": "partial",
                             "message": "Agent turn ceiling reached; returning gathered evidence as a partial investigation."})
            return RCAReport(status="incomplete",
                             summary="The investigation reached its agent-turn safety ceiling before it could produce a complete report.",
                             hypotheses=[], evidence=list(evidence_by_id.values()),
                             unresolved_questions=["What explanation best fits the evidence collected so far?"],
                             tool_trace=trace)

    async def _compose_report(self, question: str, alert_context: dict[str, Any] | None,
                              notes: InvestigationNotes, evidence_by_id: dict[str, EvidenceRef],
                              evidence_records: dict[str, dict[str, Any]], aliases: dict[str, str],
                              trace: list[dict[str, Any]], budget_reached: bool,
                              on_progress: ProgressCallback) -> RCAReport:
        if not aliases:
            return RCAReport(status="incomplete" if budget_reached else "abstained",
                             summary="No evidence records were returned, so no cause can be supported.",
                             hypotheses=[], evidence=[],
                             unresolved_questions=notes.unresolved_questions or ["Which system record can explain the signal?"],
                             tool_trace=trace)

        report_type = _constrained_report_type(list(aliases))
        citation_ids_by_evidence = {evidence_id: citation_id
                                    for citation_id, evidence_id in aliases.items()}
        evidence_index = []
        by_source: dict[str, list[dict[str, Any]]] = {}
        for evidence_id, record in evidence_records.items():
            citation_id = citation_ids_by_evidence.get(evidence_id)
            if citation_id is None:
                continue
            source = str(record.get("source", "unknown"))
            by_source.setdefault(source, []).append({
                "citation_id": citation_id, "source": source,
                "observed_at": record.get("observed_at"), "event_type": record.get("event_type"),
                "record": record.get("payload", {}),
            })
        for source_records in by_source.values():
            evidence_index.extend(source_records[-8:])

        if on_progress:
            on_progress({"type": "status", "stage": "synthesizing_report",
                         "message": "Checking candidate explanations against the returned evidence."})
        composer = Agent(
            name="RCA Report Composer", model=self.model,
            instructions=(
                "Produce the final evidence-grounded RCA. Reassess the investigator's candidate notes against the "
                "actual records; do not repeat unsupported claims. Only cite citation_id values present in the "
                "evidence index. Structured output restricts citations to those values. Rank plausible causes, "
                "include contradictory evidence when present, and abstain when support is weak. Describe the cause "
                "as evidence-supported or consistent with the records, never as proven causality."
            ),
            output_type=report_type,
        )
        result = await Runner.run(
            composer,
            input=json.dumps({"operator_question": question, "selected_signal": alert_context,
                              "candidate_notes": notes.model_dump(), "evidence_index": evidence_index},
                             ensure_ascii=False),
            max_turns=2,
        )
        content = result.final_output
        if not isinstance(content, report_type):
            raise ValueError("The report composer did not return the constrained RCA schema.")
        valid_ids = set(evidence_by_id)
        hypotheses = []
        for item in content.hypotheses:
            supporting = [_resolve_citation(value, valid_ids, aliases) for value in item.supporting_evidence]
            contradicting = [_resolve_citation(value, valid_ids, aliases) for value in item.contradicting_evidence]
            supporting = [value for value in supporting if value]
            contradicting = [value for value in contradicting if value]
            if supporting:
                hypotheses.append(Hypothesis(item.title, item.explanation, item.rank, supporting, contradicting))
        if hypotheses:
            status = "incomplete" if budget_reached else "completed"
            summary = content.summary
            if budget_reached:
                summary = "Evidence search reached its safety ceiling; this investigation is partial. " + summary
            unresolved = content.unresolved_questions
        else:
            status = "incomplete" if budget_reached else "abstained"
            summary = "No cause is assigned because the available evidence did not support a cited hypothesis."
            unresolved = content.unresolved_questions or notes.unresolved_questions
        return RCAReport(status=status, summary=summary, hypotheses=hypotheses,
                         evidence=list(evidence_by_id.values()), unresolved_questions=unresolved,
                         tool_trace=trace)


def _constrained_report_type(citation_ids: list[str]) -> type[BaseModel]:
    """Build a report schema whose citation fields accept only returned evidence handles."""
    citation_type = Literal.__getitem__(tuple(citation_ids))
    cited_hypothesis = create_model(
        "CitedHypothesis",
        __config__=ConfigDict(extra="forbid"),
        title=(str, ...),
        explanation=(str, ...),
        rank=(int, Field(ge=1, le=3)),
        supporting_evidence=(list[citation_type], ...),
        contradicting_evidence=(list[citation_type], ...),
    )
    return create_model(
        "CitedRCAOutput",
        __config__=ConfigDict(extra="forbid"),
        summary=(str, ...),
        hypotheses=(list[cited_hypothesis], ...),
        unresolved_questions=(list[str], ...),
    )


def _collect_evidence_records(value: Any, target: dict[str, dict[str, Any]]) -> None:
    """Keep the actual returned records for constrained final synthesis."""
    if isinstance(value, dict):
        evidence_id = value.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id not in target:
            target[evidence_id] = value
        for child in value.values():
            _collect_evidence_records(child, target)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_evidence_records(child, target)


def _tool_summary(result: Any) -> str:
    if isinstance(result, dict):
        records = next((value for value in result.values() if isinstance(value, list)), [])
        return f"Found {len(records)} matching records." if records else str(result.get("error", "Search completed; no matching records."))
    return "Search completed."


def _with_citation_aliases(value: Any, aliases: dict[str, str], reverse: dict[str, str]) -> Any:
    """Give the model short per-investigation citation handles instead of UUIDs."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        evidence_id = value.get("evidence_id")
        if isinstance(evidence_id, str):
            alias = aliases.get(evidence_id)
            if alias is None:
                alias = f"E{len(aliases) + 1}"
                aliases[evidence_id] = alias
                reverse[alias] = evidence_id
        for key, child in value.items():
            if key == "evidence_id" and isinstance(evidence_id, str):
                result["citation_id"] = aliases[evidence_id]
            else:
                result[key] = _with_citation_aliases(child, aliases, reverse)
        return result
    if isinstance(value, list):
        return [_with_citation_aliases(child, aliases, reverse) for child in value]
    return value


def _resolve_citation(value: str, valid_ids: set[str], aliases: dict[str, str]) -> str | None:
    if value in valid_ids:
        return value
    normalized = value.strip().upper()
    resolved = aliases.get(normalized)
    if resolved is None:
        match = re.fullmatch(r"(?:CITATION\s*)?E\s*0*(\d+)", normalized)
        if match:
            resolved = aliases.get(f"E{int(match.group(1))}")
    return resolved if resolved in valid_ids else None


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
