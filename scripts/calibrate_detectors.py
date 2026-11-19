"""Calibrate per-method thresholds using only designated control seeds."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from agentic_ops.detectors import CountDetectorSuite, DetectorConfig
from agentic_ops.domain import SimulationConfig
from agentic_ops.simulation import generate_run

METHODS = ("poisson_llr", "negative_binomial_llr", "standardized_residual", "cusum",
           "gamma_poisson_bayes_factor")
CONTROLS = ("healthy", "unrelated_issue", "resolved_issue")


def _quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        return 1.0
    index = min(len(sorted_values) - 1, max(0, math.ceil(probability * len(sorted_values)) - 1))
    return sorted_values[index]


def calibrate(seed_count: int = 100, target_combined_alerts_per_day: float = 0.1) -> dict:
    threshold_bundle: dict[str, dict[str, float]] = {}
    method_alert_budget = target_combined_alerts_per_day / len(METHODS)
    tail = method_alert_budget / 48.0
    quantile_probability = 1.0 - tail
    for arrival_model in ("poisson", "negative_binomial"):
        samples: dict[str, list[float]] = defaultdict(list)
        for scenario in CONTROLS:
            for seed in range(seed_count):
                config = SimulationConfig(scenario=scenario, seed=seed, arrival_model=arrival_model)
                suite = CountDetectorSuite(DetectorConfig(warmup_windows=config.calibration_windows,
                                                          score_model=arrival_model))
                for window in generate_run(config):
                    scores, _ = suite.update(window.ticket_count, window.index)
                    for score in scores:
                        samples[score.detector].append(score.score)
        threshold_bundle[arrival_model] = {
            method: _quantile(sorted(samples.get(method, [])), quantile_probability)
            for method in METHODS
        }
    return {
        "version": 1,
        "calibration_seed_range": [0, seed_count - 1],
        "control_scenarios": list(CONTROLS),
        "target_combined_alert_episodes_per_day": target_combined_alerts_per_day,
        "per_method_tail_budget_per_day": method_alert_budget,
        "method_thresholds": threshold_bundle,
        "thresholds": threshold_bundle,
        "note": "Empirical thresholds from synthetic controls; re-calibrate when the generator or detector changes.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-count", type=int, default=100)
    parser.add_argument("--target-alerts-per-day", type=float, default=0.1)
    parser.add_argument("--output", type=Path, default=Path("configs/detector_thresholds.json"))
    arguments = parser.parse_args()
    result = calibrate(arguments.seed_count, arguments.target_alerts_per_day)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote calibration bundle to {arguments.output}")
