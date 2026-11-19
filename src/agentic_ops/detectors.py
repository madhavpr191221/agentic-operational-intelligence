"""Count anomaly methods with intentionally separate score semantics."""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from pathlib import Path

from agentic_ops.domain import Score


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


def calibrated_config(model: str, warmup_windows: int = 48,
                     thresholds_path: str | Path | None = None) -> DetectorConfig:
    """Load model-specific thresholds produced by the calibration script."""
    path = Path(thresholds_path) if thresholds_path else Path(__file__).resolve().parents[2] / "configs" / "detector_thresholds.json"
    values: dict[str, float] = {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("thresholds", {}).get(model, {})
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
    )


class CountDetectorSuite:
    """Fits a baseline during warmup, then returns multiple candidate scores."""

    def __init__(self, config: DetectorConfig | None = None):
        self.config = config or DetectorConfig()
        self.baseline: list[int] = []
        self.expected = 0.0
        self.nb_shape = self.config.fallback_nb_shape
        self.cusum = 0.0

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

        poisson_score = poisson_log_likelihood_ratio(observed, self.expected)
        nb_score = negative_binomial_log_likelihood_ratio(observed, self.expected, self.nb_shape)
        poisson_z = standardized_residual(observed, self.expected, self.expected)
        nb_variance = self.expected + self.expected**2 / self.nb_shape
        nb_z = standardized_residual(observed, self.expected, nb_variance)
        active_z = nb_z if self.config.score_model == "negative_binomial" else poisson_z
        self.cusum = max(0.0, self.cusum + active_z - self.config.cusum_reference)
        bayes = gamma_poisson_log_bayes_factor(observed, max(self.expected, 0.05))

        scores = [
            Score("poisson_llr", "log_likelihood_ratio", poisson_score, self.config.poisson_llr_threshold,
                  self.expected, observed, poisson_score >= self.config.poisson_llr_threshold,
                  "One-sided Poisson rate-increase evidence; this is not a Bayes factor."),
            Score("negative_binomial_llr", "log_likelihood_ratio", nb_score,
                  self.config.negative_binomial_llr_threshold, self.expected, observed,
                  nb_score >= self.config.negative_binomial_llr_threshold,
                  f"NB upward likelihood ratio; fitted baseline shape r={self.nb_shape:.3g}."),
            Score("standardized_residual", "z_score_poisson", poisson_z, self.config.residual_threshold,
                  self.expected, observed, poisson_z >= self.config.residual_threshold,
                  "Poisson model residual; threshold is calibrated empirically."),
            Score("cusum", "positive_cusum", self.cusum, self.config.cusum_threshold,
                  self.expected, observed, self.cusum >= self.config.cusum_threshold,
                  f"Accumulated positive {self.config.score_model} residual excess."),
            Score("gamma_poisson_bayes_factor", "log_bayes_factor", bayes,
                  self.config.log_bayes_factor_threshold, self.expected, observed,
                  bayes >= self.config.log_bayes_factor_threshold,
                  "Gamma-Poisson prior predictive comparison; alternative prior mean is 2x baseline."),
        ]
        return scores, any(score.anomalous for score in scores)
