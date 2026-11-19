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

SCENARIOS = ("payment_issue", "healthy", "unrelated_issue", "resolved_issue")


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


def _has_positive_evidence(report: dict, records: dict[str, dict]) -> bool:
    if not report.get("hypotheses"):
        return False
    top = min(report["hypotheses"], key=lambda item: item.get("rank", 99))
    cited = [records[evidence_id] for evidence_id in top.get("supporting_evidence", [])
             if evidence_id in records]
    payment_ticket = any(item.get("payload", {}).get("category") == "payments"
                         for item in cited)
    payment_log = any(item.get("payload", {}).get("service") == "checkout-payments"
                      and item.get("payload", {}).get("errors", 0) > 0 for item in cited)
    open_issue = any(item.get("payload", {}).get("service") == "checkout-payments"
                     and item.get("payload", {}).get("status") == "open" for item in cited)
    return payment_ticket and payment_log and open_issue


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
                observations = runtime.store.search_events(
                    run["run_id"], ("detector",), limit=1, event_types=("window_observation",)
                )
                last = observations[-1]
                signal = {
                    "metric": "support_ticket_arrivals", "category": "payments",
                    "window_index": last["payload"]["window_index"],
                    "window_start": last["payload"]["window_start"],
                    "window_end": last["payload"]["window_end"], "window_minutes": 30,
                    "observed": 74, "expected": 4.0, "arrival_model": "poisson",
                    "scores": {"z_score": 8.4, "cusum": 12.1,
                               "log_likelihood_ratio": 9.7},
                }
                context = {"metric": signal["metric"], "category": signal["category"],
                           "window_index": signal["window_index"],
                           "window_start": signal["window_start"], "window_end": signal["window_end"],
                           "signal": signal}
                started = time.perf_counter()
                report = investigator.investigate(
                    run["run_id"], "Investigate the selected payments ticket signal using available evidence.",
                    runtime.evidence, context,
                ).to_dict()
                duration = time.perf_counter() - started
                records = _records_from_trace(report)
                citations = [evidence_id for hypothesis in report.get("hypotheses", [])
                             for evidence_id in (hypothesis.get("supporting_evidence", [])
                                                + hypothesis.get("contradicting_evidence", []))]
                valid_citations = sum(evidence_id in records for evidence_id in citations)
                positive_support = _has_positive_evidence(report, records)
                unsupported_payment_claim = scenario != "payment_issue" and any(
                    "payment" in (hypothesis.get("title", "") + " " + hypothesis.get("explanation", "")).casefold()
                    and not positive_support
                    for hypothesis in report.get("hypotheses", [])
                )
                rows.append({
                    "scenario": scenario, "seed": seed, "status": report["status"],
                    "positive_cause_supported": positive_support,
                    "unsupported_payment_claim": unsupported_payment_claim,
                    "abstained": not bool(report.get("hypotheses")),
                    "valid_citations": valid_citations, "citations": len(citations),
                    "tool_calls": len(report.get("tool_trace", [])),
                    "duration_seconds": round(duration, 2),
                })
    positives = [row for row in rows if row["scenario"] == "payment_issue"]
    controls = [row for row in rows if row["scenario"] != "payment_issue"]
    summary = {
        "cases": len(rows),
        "positive_cause_support_rate": sum(row["positive_cause_supported"] for row in positives) / max(1, len(positives)),
        "control_unsupported_payment_claim_rate": sum(row["unsupported_payment_claim"] for row in controls) / max(1, len(controls)),
        "control_abstention_rate": sum(row["abstained"] for row in controls) / max(1, len(controls)),
        "citation_validity_rate": (sum(row["valid_citations"] for row in rows)
                                   / max(1, sum(row["citations"] for row in rows))),
        "mean_tool_calls": sum(row["tool_calls"] for row in rows) / max(1, len(rows)),
        "mean_duration_seconds": round(sum(row["duration_seconds"] for row in rows) / max(1, len(rows)), 2),
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
