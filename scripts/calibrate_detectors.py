"""Calibrate category detector thresholds on independent seeded controls."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from agentic_ops.detectors import CountDetectorSuite, DetectorConfig
from agentic_ops.domain import SimulationConfig
from agentic_ops.simulation import CATEGORIES, generate_run

METHODS = ("poisson_llr", "negative_binomial_llr", "standardized_residual", "cusum",
           "gamma_poisson_bayes_factor")
BASELINES = ("fixed", "ewma")
CONTROLS = ("healthy", "unrelated_issue", "resolved_issue")


def _upper_threshold(values: list[float], upper_tail_probability: float) -> float:
    """Return a conservative empirical upper-tail cutoff for >= comparisons."""
    if not values:
        return 1.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil((1 - upper_tail_probability) * len(ordered)) - 1))
    # Scores can be discrete and tied. Strictly exceed the empirical quantile
    # so ties at the boundary do not silently inflate the planned tail budget.
    return math.nextafter(ordered[index], math.inf)


def calibrate(seed_count: int = 500, target_combined_alerts_per_day: float = 0.1) -> dict:
    if seed_count < 1:
        raise ValueError("seed_count must be positive")
    if target_combined_alerts_per_day <= 0:
        raise ValueError("target_combined_alerts_per_day must be positive")

    threshold_bundle: dict[str, dict[str, dict[str, float]]] = {}
    category_bundle: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    per_series_tail = target_combined_alerts_per_day / (
        48 * len(CATEGORIES) * len(METHODS)
    )

    for baseline_mode in BASELINES:
        threshold_bundle[baseline_mode] = {}
        category_bundle[baseline_mode] = {}
        for arrival_model in ("poisson", "negative_binomial"):
            total_samples: dict[str, list[float]] = defaultdict(list)
            category_samples: dict[str, dict[str, list[float]]] = {
                category: defaultdict(list) for category in CATEGORIES
            }
            for scenario in CONTROLS:
                for seed in range(seed_count):
                    config = SimulationConfig(scenario=scenario, seed=seed, arrival_model=arrival_model)
                    suites = {
                        category: CountDetectorSuite(DetectorConfig(
                            warmup_windows=config.calibration_windows,
                            score_model=arrival_model,
                            baseline_mode=baseline_mode,
                            poisson_llr_threshold=math.inf,
                            negative_binomial_llr_threshold=math.inf,
                            residual_threshold=math.inf,
                            cusum_threshold=math.inf,
                            log_bayes_factor_threshold=math.inf,
                        ))
                        for category in CATEGORIES
                    }
                    total_suite = CountDetectorSuite(DetectorConfig(
                        warmup_windows=config.calibration_windows,
                        score_model=arrival_model,
                        baseline_mode=baseline_mode,
                        poisson_llr_threshold=math.inf,
                        negative_binomial_llr_threshold=math.inf,
                        residual_threshold=math.inf,
                        cusum_threshold=math.inf,
                        log_bayes_factor_threshold=math.inf,
                    ))
                    for window in generate_run(config, include_ticket_events=False):
                        # The detector calibration receives only the count vector.
                        total_scores, _ = total_suite.update(window.ticket_count, window.index)
                        for score in total_scores:
                            total_samples[score.detector].append(score.score)
                        for category, observed in window.category_counts.items():
                            scores, _ = suites[category].update(observed, window.index)
                            for score in scores:
                                category_samples[category][score.detector].append(score.score)

            category_bundle[baseline_mode][arrival_model] = {
                category: {
                    method: _upper_threshold(
                        category_samples[category][method], per_series_tail
                    )
                    for method in METHODS
                }
                for category in CATEGORIES
            }
            threshold_bundle[baseline_mode][arrival_model] = {
                method: _upper_threshold(
                    total_samples[method],
                    per_series_tail,
                )
                for method in METHODS
            }

    return {
        "version": 3,
        "calibration_seed_range": [0, seed_count - 1],
        "control_scenarios": list(CONTROLS),
        "target_combined_alert_episodes_per_day": target_combined_alerts_per_day,
        "per_method_category_window_tail_budget": per_series_tail,
        "arrival_models": ["poisson", "negative_binomial"],
        "baseline_modes": list(BASELINES),
        "ewma_lambda": 0.2,
        "method_thresholds": threshold_bundle,
        "thresholds": threshold_bundle,
        "category_thresholds": category_bundle,
        "categories": list(CATEGORIES),
        "note": (
            "Empirical one-window thresholds allocated across five methods and four categories. "
            "Validate the combined episode rate on held-out control seeds; recalibrate when "
            "the generator or detector changes."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-count", type=int, default=500)
    parser.add_argument("--target-alerts-per-day", type=float, default=0.1)
    parser.add_argument("--output", type=Path, default=Path("configs/detector_thresholds.json"))
    arguments = parser.parse_args()
    result = calibrate(arguments.seed_count, arguments.target_alerts_per_day)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote calibration bundle to {arguments.output}")
