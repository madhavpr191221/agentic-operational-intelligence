"""Count-only statistical detection package."""

from agentic_ops.detection.contracts import (
    CategoryDetection,
    CountObservation,
    DetectionResult,
    Score,
)
from agentic_ops.detection.engine import (
    AlertEpisodeTracker,
    CategoryDetectorSuite,
    CountDetectorSuite,
    DetectorConfig,
    calibrated_config,
    gamma_poisson_log_bayes_factor,
    negative_binomial_log_likelihood_ratio,
    poisson_log_likelihood_ratio,
    standardized_residual,
)

__all__ = [
    "AlertEpisodeTracker",
    "CategoryDetection",
    "CategoryDetectorSuite",
    "CountDetectorSuite",
    "CountObservation",
    "DetectionResult",
    "DetectorConfig",
    "Score",
    "calibrated_config",
    "gamma_poisson_log_bayes_factor",
    "negative_binomial_log_likelihood_ratio",
    "poisson_log_likelihood_ratio",
    "standardized_residual",
]
