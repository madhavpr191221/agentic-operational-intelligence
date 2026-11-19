"""Evaluate calibrated candidate scores on held-out seeded scenarios."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from agentic_ops.detectors import CategoryDetectorSuite
from agentic_ops.domain import SimulationConfig
from agentic_ops.simulation import CATEGORIES, generate_run

METHODS = ("poisson_llr", "negative_binomial_llr", "standardized_residual", "cusum",
           "gamma_poisson_bayes_factor")
CONTROLS = ("healthy", "unrelated_issue", "resolved_issue")


def _run_scores(config: SimulationConfig) -> dict[str, dict[str, list[tuple[int, float, bool]]]]:
    suite = CategoryDetectorSuite(config.arrival_model, config.calibration_windows)
    results: dict[str, dict[str, list[tuple[int, float, bool]]]] = {
        category: defaultdict(list) for category in CATEGORIES
    }
    for window in generate_run(config):
        category_scores = suite.update(window.category_counts, window.index)
        for category, (scores, _) in category_scores.items():
            for score in scores:
                results[category][score.detector].append((window.index, score.score, score.anomalous))
    return results


def _episode_count(values: list[tuple[int, float, bool]]) -> int:
    count = 0
    previous = False
    for _, _, flagged in values:
        if flagged and not previous:
            count += 1
        previous = flagged
    return count


def evaluate(heldout_seeds: int = 50, first_seed: int = 100) -> dict:
    results: dict[str, dict[str, float]] = {}
    for arrival_model in ("poisson", "negative_binomial"):
        method_detected = {method: 0 for method in METHODS}
        method_timely = {method: 0 for method in METHODS}
        method_delays = {method: [] for method in METHODS}
        method_false_episodes = {method: 0 for method in METHODS}
        combined_false_episodes = 0
        control_days = 0.0
        for scenario in (*CONTROLS, "payment_issue"):
            for seed in range(first_seed, first_seed + heldout_seeds):
                config = SimulationConfig(scenario=scenario, seed=seed, arrival_model=arrival_model)
                values = _run_scores(config)
                scored_days = (config.window_count - config.calibration_windows) / 48
                if scenario in CONTROLS:
                    control_days += scored_days
                    for category in CATEGORIES:
                        for method in METHODS:
                            method_false_episodes[method] += _episode_count(values[category].get(method, []))
                    union = []
                    for index in range(config.calibration_windows, config.window_count):
                        flagged = any(
                            next((flag for idx, _, flag in values[category].get(method, []) if idx == index), False)
                            for category in CATEGORIES for method in METHODS
                        )
                        union.append((index, 0.0, flagged))
                    combined_false_episodes += _episode_count(union)
                else:
                    onset = config.window_count - 4
                    values = values["payments"]
                    for method in METHODS:
                        flagged_windows = [index for index, _, flagged in values.get(method, []) if flagged]
                        if any(onset <= index <= onset + 2 for index in flagged_windows):
                            method_timely[method] += 1
                        if flagged_windows:
                            method_detected[method] += 1
                            first_after_onset = next((index for index in flagged_windows if index >= onset), None)
                            if first_after_onset is not None:
                                method_delays[method].append(first_after_onset - onset)
        for method in METHODS:
            results[f"{arrival_model}:{method}"] = {
                "positive_recall": method_detected[method] / heldout_seeds,
                "detected_within_two_windows": method_timely[method] / heldout_seeds,
                "control_false_alert_episodes_per_day": method_false_episodes[method] / max(control_days, 1e-9),
                "mean_delay_windows": (sum(method_delays[method]) / len(method_delays[method])
                                       if method_delays[method] else -1),
            }
        results[f"{arrival_model}:combined"] = {
            "control_false_alert_episodes_per_day": combined_false_episodes / max(control_days, 1e-9),
            "control_simulated_days": control_days,
        }
    return {"heldout_seeds_per_scenario": heldout_seeds, "heldout_first_seed": first_seed,
            "evaluated_positive_category": "payments",
            "thresholds_file": str(Path("configs/detector_thresholds.json")), "results": results}


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2))
