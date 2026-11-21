"""Evaluate calibrated count alerts on held-out seeded scenarios."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from agentic_ops.detection import CategoryDetectorSuite, CountObservation
from agentic_ops.domain import SimulationConfig
from agentic_ops.simulation import CATEGORIES, generate_run

METHODS = ("poisson_llr", "negative_binomial_llr", "standardized_residual", "cusum",
           "gamma_poisson_bayes_factor")
BASELINES = ("fixed", "ewma")
CONTROLS = ("healthy", "unrelated_issue", "resolved_issue")


def _episode_count(values: list[tuple[int, bool]]) -> int:
    count = 0
    previous_index: int | None = None
    previous_alerted = False
    for index, alerted in values:
        if alerted and (not previous_alerted or previous_index != index - 1):
            count += 1
        previous_index = index
        previous_alerted = alerted
    return count


def _run_scores(config: SimulationConfig, thresholds_path: str | Path | None = None
                ) -> dict[str, dict[str, dict[str, list[tuple[int, bool]]]]]:
    suites = {
        baseline: CategoryDetectorSuite(
            config.arrival_model, config.calibration_windows, CATEGORIES, baseline, thresholds_path
        )
        for baseline in BASELINES
    }
    results = {
        baseline: {
            category: {"combined": [], **{method: [] for method in METHODS}}
            for category in CATEGORIES
        }
        for baseline in BASELINES
    }
    for window in generate_run(config, include_ticket_events=False):
        for baseline, suite in suites.items():
            category_results = suite.update(CountObservation(
                window.index, window.start, window.end, window.category_counts
            ))
            for category, result in category_results.categories.items():
                results[baseline][category]["combined"].append((window.index, result.alerted))
                for score in result.scores:
                    results[baseline][category][score.detector].append(
                        (window.index, score.anomalous)
                    )
    return results


def evaluate(heldout_seeds: int = 200, first_seed: int = 1000,
             thresholds_path: str | Path = "configs/detector_thresholds.json") -> dict:
    try:
        threshold_metadata = json.loads(Path(thresholds_path).read_text(encoding="utf-8"))
        calibration_seed_range = threshold_metadata.get("calibration_seed_range")
    except (OSError, json.JSONDecodeError):
        calibration_seed_range = None
    results: dict[str, dict[str, float | int | dict[str, float | int]]] = {}
    for arrival_model in ("poisson", "negative_binomial"):
        accumulators = {
            baseline: {
                "control_episodes": 0,
                "control_days": 0.0,
                "episodes_by_control": defaultdict(int),
                "incident_detected": 0,
                "incident_timely": 0,
                "incident_delays": [],
                "nonpayment_incident_episodes": 0,
                "method_timely": {method: 0 for method in METHODS},
            }
            for baseline in BASELINES
        }
        for scenario in (*CONTROLS, "payment_issue"):
            for seed in range(first_seed, first_seed + heldout_seeds):
                config = SimulationConfig(scenario=scenario, seed=seed, arrival_model=arrival_model)
                values_by_baseline = _run_scores(config, thresholds_path)
                scored_days = (config.window_count - config.calibration_windows) / 48
                for baseline, values in values_by_baseline.items():
                    stats = accumulators[baseline]
                    if scenario in CONTROLS:
                        stats["control_days"] += scored_days
                        scenario_episodes = sum(
                            _episode_count(values[category]["combined"])
                            for category in CATEGORIES
                        )
                        stats["control_episodes"] += scenario_episodes
                        stats["episodes_by_control"][scenario] += scenario_episodes
                        continue

                    onset = config.window_count - 4
                    first_alert = next((
                        index for index, flagged in values["payments"]["combined"]
                        if index >= onset and flagged
                    ), None)
                    if first_alert is not None:
                        stats["incident_detected"] += 1
                        delay = first_alert - onset
                        stats["incident_delays"].append(delay)
                        if delay <= 2:
                            stats["incident_timely"] += 1
                    stats["nonpayment_incident_episodes"] += sum(
                        _episode_count([
                            (index, flagged) for index, flagged in values[category]["combined"]
                            if index >= onset
                        ])
                        for category in CATEGORIES if category != "payments"
                    )
                    for method in METHODS:
                        first_method_alert = next((
                            index for index, flagged in values["payments"][method]
                            if index >= onset and flagged
                        ), None)
                        if first_method_alert is not None and first_method_alert - onset <= 2:
                            stats["method_timely"][method] += 1

        for baseline, stats in accumulators.items():
            delays = stats["incident_delays"]
            control_days = stats["control_days"]
            results[f"{baseline}:{arrival_model}"] = {
                "control_false_alert_episodes_per_day": stats["control_episodes"] / max(control_days, 1e-9),
                "control_false_alert_episodes": stats["control_episodes"],
                "control_simulated_days": int(control_days),
                "control_episodes_by_scenario": dict(stats["episodes_by_control"]),
                "payment_incident_detection_rate": stats["incident_detected"] / heldout_seeds,
                "payment_detected_within_two_windows": stats["incident_timely"] / heldout_seeds,
                "mean_detection_delay_windows": sum(delays) / len(delays) if delays else -1.0,
                "nonpayment_category_episodes_during_payment_incident": stats["nonpayment_incident_episodes"],
                "method_detection_within_two_windows": {
                    method: count / heldout_seeds for method, count in stats["method_timely"].items()
                },
            }
    return {
        "heldout_seeds_per_scenario": heldout_seeds,
        "heldout_seed_range": [first_seed, first_seed + heldout_seeds - 1],
        "calibration_seed_range": calibration_seed_range,
        "target_false_alert_episodes_per_day": 0.1,
        "target_payment_detection_within_two_windows": 0.95,
        "episode_definition": "Consecutive flagged windows per category; a quiet window ends the statistical grouping only.",
        "thresholds_file": str(thresholds_path),
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--heldout-seeds", type=int, default=200)
    parser.add_argument("--first-seed", type=int, default=1000)
    parser.add_argument("--thresholds", type=Path, default=Path("configs/detector_thresholds.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.heldout_seeds, args.first_seed, args.thresholds)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
