"""Count anomaly methods with intentionally separate score semantics."""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from agentic_ops.detection.contracts import (
    CategoryDetection,
    CountObservation,
    DetectionResult,
    Score,
)


def poisson_log_likelihood_ratio(observed: int, expected: float) -> float:
    """One-sided generalized log-likelihood ratio for an elevated Poisson rate."""
    if expected <= 0:
        return 0.0 if observed <= 0 else math.inf
    if observed <= expected:
        return 0.0
    return observed * math.log(observed / expected) - (observed - expected)


def _negative_binomial_log_pmf(observed: int, mean: float, shape: float) -> float:
    if mean <= 0:
        return 0.0 if observed == 0 else -math.inf
    p = shape / (shape + mean)
    return (math.lgamma(observed + shape) - math.lgamma(shape) - math.lgamma(observed + 1)
            + shape * math.log(p) + observed * math.log1p(-p))


def negative_binomial_log_likelihood_ratio(observed: int, expected: float, shape: float) -> float:
    if observed <= expected:
        return 0.0
    return _negative_binomial_log_pmf(observed, float(observed), shape) - _negative_binomial_log_pmf(observed, expected, shape)


def standardized_residual(observed: int, expected: float, variance: float) -> float:
    return (observed - expected) / math.sqrt(max(variance, 1e-12))


def gamma_poisson_log_bayes_factor(
    observed: int, baseline_mean: float, prior_shape: float = 2.0, prior_mean_multiplier: float = 2.0
) -> float:
    """Log BF: fixed-rate Poisson null vs Gamma-Poisson higher-rate alternative.

    The alternative rate prior is Gamma(shape, rate=shape/(multiplier*baseline)).
    The prior is explicit and must be held fixed for held-out evaluation.
    """
    if baseline_mean <= 0 or prior_shape <= 0 or prior_mean_multiplier <= 0:
        raise ValueError("baseline and prior parameters must be positive")
    rate = prior_shape / (prior_mean_multiplier * baseline_mean)
    log_marginal_alt = (
        math.lgamma(observed + prior_shape) - math.lgamma(prior_shape) - math.lgamma(observed + 1)
        + prior_shape * math.log(rate / (rate + 1.0))
        + observed * math.log(1.0 / (rate + 1.0))
    )
    log_likelihood_null = observed * math.log(baseline_mean) - baseline_mean - math.lgamma(observed + 1)
    return log_marginal_alt - log_likelihood_null


@dataclass
class DetectorConfig:
    warmup_windows: int = 48
    poisson_llr_threshold: float = 5.0
    negative_binomial_llr_threshold: float = 5.0
    residual_threshold: float = 3.0
    cusum_reference: float = 0.5
    cusum_threshold: float = 5.0
    log_bayes_factor_threshold: float = math.log(10.0)
    fallback_nb_shape: float = 4.0
    score_model: str = "poisson"
    baseline_mode: str = "fixed"
    ewma_lambda: float = 0.2

    def __post_init__(self) -> None:
        if self.baseline_mode not in {"fixed", "ewma"}:
            raise ValueError("baseline_mode must be 'fixed' or 'ewma'")
        if not 0.0 < self.ewma_lambda <= 1.0:
            raise ValueError("ewma_lambda must be in (0, 1]")


def calibrated_config(model: str, warmup_windows: int = 48,
                      thresholds_path: str | Path | None = None,
                      category: str | None = None,
                      baseline_mode: str = "fixed") -> DetectorConfig:
    """Load model-specific thresholds produced by the calibration script."""
    path = (Path(thresholds_path) if thresholds_path else
            Path(__file__).resolve().parents[3] / "configs" / "detector_thresholds.json")
    values: dict[str, float] = {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        thresholds = payload.get("thresholds", {})
        model_values = thresholds.get(baseline_mode, {}).get(model)
        if model_values is None:
            model_values = thresholds.get(model, {})
        values = model_values or {}
        if category:
            category_values = payload.get("category_thresholds", {}).get(baseline_mode, {}).get(model)
            if category_values is None:
                category_values = payload.get("category_thresholds", {}).get(model, {})
            values = (category_values or {}).get(category, values)
    except (OSError, json.JSONDecodeError):
        values = {}
    return DetectorConfig(
        warmup_windows=warmup_windows,
        score_model=model,
        poisson_llr_threshold=float(values.get("poisson_llr", 5.0)),
        negative_binomial_llr_threshold=float(values.get("negative_binomial_llr", 5.0)),
        residual_threshold=float(values.get("standardized_residual", 3.0)),
        cusum_threshold=float(values.get("cusum", 5.0)),
        log_bayes_factor_threshold=float(values.get("gamma_poisson_bayes_factor", math.log(10.0))),
        baseline_mode=baseline_mode,
    )


class CountDetectorSuite:
    """Fits a baseline during warmup, then returns multiple candidate scores."""

    def __init__(self, config: DetectorConfig | None = None):
        self.config = config or DetectorConfig()
        self.baseline: list[int] = []
        self.expected = 0.0
        self.nb_shape = self.config.fallback_nb_shape
        self.cusum = 0.0
        self.baseline_frozen = False

    def update(self, observed: int, index: int) -> tuple[list[Score], bool]:
        if observed < 0:
            raise ValueError("observed count cannot be negative")
        if index < self.config.warmup_windows:
            self.baseline.append(observed)
            self.expected = sum(self.baseline) / len(self.baseline)
            return [], False
        if index == self.config.warmup_windows:
            if self.baseline:
                self.expected = sum(self.baseline) / len(self.baseline)
                variance = sum((x - self.expected) ** 2 for x in self.baseline) / len(self.baseline)
                excess = variance - self.expected
                if excess > 1e-9:
                    self.nb_shape = min(1000.0, max(0.25, self.expected**2 / excess))

        expected = self.expected
        poisson_score = poisson_log_likelihood_ratio(observed, expected)
        nb_score = negative_binomial_log_likelihood_ratio(observed, expected, self.nb_shape)
        poisson_z = standardized_residual(observed, expected, expected)
        nb_variance = expected + expected**2 / self.nb_shape
        nb_z = standardized_residual(observed, expected, nb_variance)
        active_z = nb_z if self.config.score_model == "negative_binomial" else poisson_z
        self.cusum = max(0.0, self.cusum + active_z - self.config.cusum_reference)
        bayes = gamma_poisson_log_bayes_factor(observed, max(expected, 0.05))

        scores = [
            Score("poisson_llr", "log_likelihood_ratio", poisson_score, self.config.poisson_llr_threshold,
                  expected, observed, poisson_score >= self.config.poisson_llr_threshold,
                  "One-sided Poisson rate-increase evidence; this is not a Bayes factor."),
            Score("negative_binomial_llr", "log_likelihood_ratio", nb_score,
                  self.config.negative_binomial_llr_threshold, expected, observed,
                  nb_score >= self.config.negative_binomial_llr_threshold,
                  f"NB upward likelihood ratio; fitted baseline shape r={self.nb_shape:.3g}."),
            Score("standardized_residual",
                  "z_score_negative_binomial" if self.config.score_model == "negative_binomial" else "z_score_poisson",
                  active_z, self.config.residual_threshold,
                  expected, observed, active_z >= self.config.residual_threshold,
                  f"{self.config.score_model} model residual; threshold is calibrated empirically."),
            Score("cusum", "positive_cusum", self.cusum, self.config.cusum_threshold,
                  expected, observed, self.cusum >= self.config.cusum_threshold,
                  f"Accumulated positive {self.config.score_model} residual excess."),
            Score("gamma_poisson_bayes_factor", "log_bayes_factor", bayes,
                  self.config.log_bayes_factor_threshold, expected, observed,
                  bayes >= self.config.log_bayes_factor_threshold,
                  "Gamma-Poisson prior predictive comparison; alternative prior mean is 2x baseline."),
        ]
        alerted = any(score.anomalous for score in scores)
        if alerted:
            self.baseline_frozen = True
        elif self.config.baseline_mode == "ewma" and not self.baseline_frozen:
            # Score against the prior estimate, then update it for the next
            # window. A detected shift cannot train itself into the baseline.
            weight = self.config.ewma_lambda
            self.expected = weight * observed + (1.0 - weight) * expected
        return scores, alerted


class CategoryDetectorSuite:
    """Independent count detector state for each support category."""

    def __init__(self, model: str, warmup_windows: int, categories: Sequence[str],
                 baseline_mode: str = "fixed",
                 thresholds_path: str | Path | None = None):
        if baseline_mode not in {"fixed", "ewma"}:
            raise ValueError("baseline_mode must be 'fixed' or 'ewma'")
        if not categories or len(set(categories)) != len(categories):
            raise ValueError("categories must be a non-empty sequence of unique names")
        self.categories = tuple(categories)
        self.suites = {
            category: CountDetectorSuite(calibrated_config(
                model, warmup_windows, thresholds_path, category, baseline_mode
            ))
            for category in self.categories
        }

    def update(self, observation: CountObservation) -> DetectionResult:
        """Score only the category counts from the supplied observation."""
        unknown = set(observation.category_counts) - set(self.suites)
        if unknown:
            raise ValueError(f"Unknown ticket categories: {', '.join(sorted(unknown))}")
        categories = {
            category: CategoryDetection(
                category=category,
                observed=int(observation.category_counts.get(category, 0)),
                expected=(scores[0].expected if scores else None),
                scores=tuple(scores),
                alerted=alerted,
            )
            for category, suite in self.suites.items()
            for scores, alerted in [suite.update(
                int(observation.category_counts.get(category, 0)), observation.window_index
            )]
        }
        return DetectionResult(
            window_index=observation.window_index,
            window_start=observation.window_start,
            window_end=observation.window_end,
            categories=categories,
        )

    def checkpoint(self) -> dict:
        """Return the count-only state required to resume scoring."""
        return {
            "version": 1,
            "categories": list(self.categories),
            "suites": {
                category: {"baseline": list(suite.baseline), "expected": suite.expected,
                           "nb_shape": suite.nb_shape, "cusum": suite.cusum,
                           "baseline_frozen": suite.baseline_frozen}
                for category, suite in self.suites.items()
            },
        }

    def restore(self, state: dict) -> None:
        """Restore a checkpoint produced for the same category configuration."""
        if state.get("version") != 1 or state.get("categories") != list(self.categories):
            raise ValueError("Incompatible detector checkpoint")
        for category, suite in self.suites.items():
            saved = state["suites"][category]
            suite.baseline = list(saved["baseline"])
            suite.expected = float(saved["expected"])
            suite.nb_shape = float(saved["nb_shape"])
            suite.cusum = float(saved["cusum"])
            suite.baseline_frozen = bool(saved["baseline_frozen"])


class AlertEpisodeTracker:
    """Group consecutive threshold crossings for each category.

    Episodes here describe statistical alert continuity only. A quiet window
    ends the grouping period; this does not represent service recovery.
    """

    def __init__(self) -> None:
        self._active: dict[str, dict[str, int | str]] = {}

    def checkpoint(self) -> dict:
        return {category: dict(episode) for category, episode in self._active.items()}

    def restore(self, state: dict) -> None:
        self._active = {category: dict(episode) for category, episode in state.items()}

    def update(self, category: str, index: int, observed: int, alerted: bool) -> dict[str, int | str] | None:
        if not alerted:
            self._active.pop(category, None)
            return None

        episode = self._active.get(category)
        if episode is None or int(episode["latest_window"]) != index - 1:
            episode = {
                "episode_id": f"{category}:{index}",
                "category": category,
                "start_window": index,
                "latest_window": index,
                "window_count": 1,
                "peak_window": index,
                "peak_ticket_count": observed,
            }
        else:
            episode["latest_window"] = index
            episode["window_count"] = int(episode["window_count"]) + 1
            if observed > int(episode["peak_ticket_count"]):
                episode["peak_window"] = index
                episode["peak_ticket_count"] = observed
        self._active[category] = episode
        return dict(episode)
