"""Run a small live RCA evaluation on synthetic cause and control evidence."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from agentic_ops.agent import OpenAIInvestigator
from agentic_ops.domain import SimulationConfig
from agentic_ops.service import OperationsRuntime, _load_openai_key_from_dotenv
from agentic_ops.store import EventStore

POSITIVE_CATEGORIES = {"payment_issue": "payments", "orders_queue_backlog": "orders",
                      "account_identity_outage": "account",
                      "delivery_carrier_degradation": "delivery"}
CONTROL_SCENARIOS = ("healthy", "unrelated_issue", "resolved_issue")
SCENARIOS = (*POSITIVE_CATEGORIES, *CONTROL_SCENARIOS)
SERVICE_BY_CATEGORY = {"payments": "checkout-payments", "orders": "orders-api",
                       "delivery": "delivery-api"}


def _records_from_trace(report: dict) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for entry in report.get("tool_trace", []):
        result = entry.get("result", {})
        if not isinstance(result, dict):
            continue
        for value in result.values():
            if isinstance(value, list):
                for record in value:
                    if isinstance(record, dict) and record.get("evidence_id"):
                        records[record["evidence_id"]] = record
    return records


def _has_positive_evidence(report: dict, records: dict[str, dict], category: str) -> bool:
    if not report.get("hypotheses"):
        return False
    top = min(report["hypotheses"], key=lambda item: item.get("rank", 99))
    cited = [records[evidence_id] for evidence_id in top.get("supporting_evidence", [])
             if evidence_id in records]
    service = SERVICE_BY_CATEGORY[category]
    category_ticket = any(item.get("payload", {}).get("category") == category for item in cited)
    def abnormal_service_record(payload: dict) -> bool:
        requests = max(1, int(payload.get("requests", 0)))
        error_rate = int(payload.get("errors", 0)) / requests
        if category == "payments":
            return error_rate >= 0.1
        if category == "orders":
            return int(payload.get("queue_depth", 0)) > 100
        return error_rate >= 0.1 or int(payload.get("p95_latency_ms", 0)) > 1000

    service_log = any(item.get("payload", {}).get("service") == service
                      and abnormal_service_record(item.get("payload", {})) for item in cited)
    latest_issue_state: dict[str, tuple[str, str]] = {}
    for item in records.values():
        payload = item.get("payload", {})
        if item.get("source") == "issue_tracker" and payload.get("service") == service:
            key = str(payload.get("issue_key", item.get("evidence_id")))
            stamp = str(item.get("observed_at", ""))
            if key not in latest_issue_state or stamp > latest_issue_state[key][0]:
                latest_issue_state[key] = (stamp, str(payload.get("status", "unknown")))
    open_issue = any(status in {"open", "investigating"} for _, status in latest_issue_state.values())
    message_support = any(item.get("payload", {}).get("category") == category
                          and item.get("payload", {}).get("body") for item in cited)
    return category_ticket and service_log and open_issue and message_support


def _episode_for_category(runtime: OperationsRuntime, run_id: str, category: str,
                          require_late_signal: bool) -> dict | None:
    alerts = runtime.store.search_events(run_id, ("detector",), limit=1000,
                                         event_types=("anomaly_alert",))
    category_alerts = sorted((item for item in alerts if item["payload"].get("category") == category),
                             key=lambda item: int(item["payload"]["window_index"]))
    if require_late_signal:
        observations = runtime.store.search_events(run_id, ("detector",), limit=1000,
                                                   event_types=("window_observation",))
        last_index = max((int(item["payload"]["window_index"]) for item in observations), default=-1)
        category_alerts = [item for item in category_alerts
                           if int(item["payload"]["window_index"]) >= last_index - 3]
    if not category_alerts:
        return None
    focus_alert = category_alerts[-1]
    focus_index = int(focus_alert["payload"]["window_index"])
    indices = {int(item["payload"]["window_index"]) for item in category_alerts}
    start_index = focus_index
    end_index = focus_index
    while start_index - 1 in indices:
        start_index -= 1
    while end_index + 1 in indices:
        end_index += 1
    summary = runtime.evidence.build_episode_summary(
        run_id, category, start_index, end_index, focus_index)
    return {"category": category, "window_index": focus_index,
            "episode_start_window_index": start_index, "episode_end_window_index": end_index,
            "focus_window_index": focus_index, "episode_summary": summary,
            "evidence_id": focus_alert["evidence_id"], **focus_alert["payload"]}


def _runbook_grounding(report: dict, records: dict[str, dict]) -> tuple[int, int]:
    actions = [item for item in report.get("recommendations", []) if item.get("runbook_id")]
    grounded = sum(bool(item.get("supporting_evidence"))
                   and set(item.get("supporting_evidence", [])) <= set(records)
                   for item in actions)
    return grounded, len(actions)


def evaluate(seed_count: int = 1, first_seed: int = 200, model: str | None = None) -> dict:
    _load_openai_key_from_dotenv()
    investigator = OpenAIInvestigator(model)
    rows: list[dict] = []
    for scenario in SCENARIOS:
        for seed in range(first_seed, first_seed + seed_count):
            with tempfile.TemporaryDirectory(prefix="aoi-rca-eval-") as directory:
                runtime = OperationsRuntime(EventStore(Path(directory) / "evaluation.sqlite3"))
                run = runtime.start_run(SimulationConfig(scenario=scenario, seed=seed,
                                                         tick_delay_seconds=0.0))
                completed = runtime.wait_for_run(run["run_id"], timeout=30.0)
                if not completed or completed["status"] != "completed":
                    raise RuntimeError(f"Synthetic run failed for {scenario}, seed {seed}.")
                category = POSITIVE_CATEGORIES.get(scenario, "payments")
                episode = _episode_for_category(runtime, run["run_id"], category,
                                                scenario in POSITIVE_CATEGORIES)
                if episode is None:
                    rows.append({"scenario": scenario, "seed": seed, "category": category,
                                 "detector_alerted": False, "status": "not_investigated",
                                 "positive_cause_supported": False, "unsupported_cause_claim": False,
                                 "abstained": None, "valid_citations": 0, "citations": 0,
                                 "analytical_tool_coverage": 0.0,
                                 "grounded_runbook_actions": 0, "runbook_actions": 0,
                                 "tool_calls": 0, "duration_seconds": 0.0})
                    continue
                context = {"selected_signals": [episode], "categories": [category],
                           "episode_summaries": [episode["episode_summary"]]}
                started = time.perf_counter()
                report = investigator.investigate(
                    run["run_id"], f"Investigate the selected {category} ticket signal using available evidence.",
                    runtime.evidence, context,
                ).to_dict()
                duration = time.perf_counter() - started
                records = _records_from_trace(report)
                citations = [evidence_id for hypothesis in report.get("hypotheses", [])
                             for evidence_id in (hypothesis.get("supporting_evidence", [])
                                                + hypothesis.get("contradicting_evidence", []))]
                valid_citations = sum(evidence_id in records for evidence_id in citations)
                is_positive = scenario in POSITIVE_CATEGORIES
                positive_support = is_positive and _has_positive_evidence(report, records, category)
                grounded_actions, runbook_actions = _runbook_grounding(report, records)
                analytical_tools = {"compare_categories", "compare_ticket_themes", "rank_ticket_contributors",
                                    "build_incident_timeline", "compare_before_after", "analyze_focus_window"}
                called_tools = {entry.get("tool") for entry in report.get("tool_trace", [])}
                unsupported_cause_claim = scenario in CONTROL_SCENARIOS and any(
                    category in hypothesis.get("categories", []) and not positive_support
                    for hypothesis in report.get("hypotheses", [])
                )
                rows.append({
                    "scenario": scenario, "seed": seed, "category": category,
                    "detector_alerted": True, "status": report["status"],
                    "positive_cause_supported": positive_support,
                    "unsupported_cause_claim": unsupported_cause_claim,
                    "abstained": not bool(report.get("hypotheses")),
                    "valid_citations": valid_citations, "citations": len(citations),
                    "analytical_tools_used": sorted(analytical_tools & called_tools),
                    "analytical_tool_coverage": len(analytical_tools & called_tools) / len(analytical_tools),
                    "grounded_runbook_actions": grounded_actions,
                    "runbook_actions": runbook_actions,
                    "runbook_grounding_rate": grounded_actions / max(1, runbook_actions),
                    "tool_calls": len(report.get("tool_trace", [])),
                    "duration_seconds": round(duration, 2),
                })
    positives = [row for row in rows if row["scenario"] in POSITIVE_CATEGORIES]
    investigated_positives = [row for row in positives if row["detector_alerted"]]
    controls = [row for row in rows if row["scenario"] in CONTROL_SCENARIOS]
    investigated_controls = [row for row in controls if row["detector_alerted"]]
    investigated_rows = [row for row in rows if row["detector_alerted"]]
    summary = {
        "cases": len(rows),
        "positive_detector_alert_rate": sum(row["detector_alerted"] for row in positives) / max(1, len(positives)),
        "positive_cause_support_rate_given_alert": sum(row["positive_cause_supported"] for row in investigated_positives) / max(1, len(investigated_positives)),
        "control_alert_episode_rate": sum(row["detector_alerted"] for row in controls) / max(1, len(controls)),
        "control_unsupported_cause_claim_rate": sum(row["unsupported_cause_claim"] for row in investigated_controls) / max(1, len(investigated_controls)),
        "control_abstention_rate_given_alert": sum(bool(row["abstained"]) for row in investigated_controls) / max(1, len(investigated_controls)),
        "citation_validity_rate": (sum(row["valid_citations"] for row in rows)
                                   / max(1, sum(row["citations"] for row in rows))),
        "mean_analytical_tool_coverage": sum(row.get("analytical_tool_coverage", 0) for row in investigated_rows) / max(1, len(investigated_rows)),
        "runbook_grounding_rate": (sum(row["grounded_runbook_actions"] for row in rows)
                                   / max(1, sum(row["runbook_actions"] for row in rows))),
        "mean_tool_calls": sum(row["tool_calls"] for row in investigated_rows) / max(1, len(investigated_rows)),
        "mean_duration_seconds": round(sum(row["duration_seconds"] for row in investigated_rows) / max(1, len(investigated_rows)), 2),
    }
    return {"model": investigator.model, "first_seed": first_seed, "seed_count_per_scenario": seed_count,
            "summary": summary, "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-count", type=int, default=1,
                        help="Synthetic seeds per scenario; each case makes a live model call.")
    parser.add_argument("--first-seed", type=int, default=200)
    parser.add_argument("--model", default=None)
    arguments = parser.parse_args()
    if arguments.seed_count < 1:
        parser.error("--seed-count must be positive")
    print(json.dumps(evaluate(arguments.seed_count, arguments.first_seed, arguments.model), indent=2))
