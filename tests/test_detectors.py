import math
import unittest

from agentic_ops.detectors import (
    CountDetectorSuite,
    DetectorConfig,
    gamma_poisson_log_bayes_factor,
    negative_binomial_log_likelihood_ratio,
    poisson_log_likelihood_ratio,
    standardized_residual,
)


class DetectorTests(unittest.TestCase):
    def test_poisson_llr_is_one_sided(self):
        self.assertEqual(poisson_log_likelihood_ratio(2, 4), 0)
        self.assertGreater(poisson_log_likelihood_ratio(20, 4), 0)

    def test_negative_binomial_llr_is_finite_and_nonnegative(self):
        score = negative_binomial_log_likelihood_ratio(20, 4, 4)
        self.assertTrue(math.isfinite(score))
        self.assertGreater(score, 0)

    def test_standardized_residual_uses_model_variance(self):
        self.assertEqual(standardized_residual(10, 4, 4), 3.0)
        self.assertAlmostEqual(standardized_residual(10, 4, 8), 6 / math.sqrt(8))

    def test_gamma_poisson_bayes_factor_is_separate_finite_score(self):
        self.assertTrue(math.isfinite(gamma_poisson_log_bayes_factor(20, 4)))
        self.assertGreater(gamma_poisson_log_bayes_factor(20, 4), gamma_poisson_log_bayes_factor(4, 4))

    def test_warmup_suppresses_alert_then_spike_triggers_candidate_scores(self):
        suite = CountDetectorSuite(DetectorConfig(warmup_windows=4))
        for index in range(4):
            scores, alert = suite.update(4, index)
            self.assertEqual(scores, [])
            self.assertFalse(alert)
        scores, alert = suite.update(30, 4)
        self.assertTrue(alert)
        self.assertEqual({"poisson_llr", "negative_binomial_llr", "standardized_residual",
                          "cusum", "gamma_poisson_bayes_factor"}, {score.detector for score in scores})

    def test_cusum_accumulates_persistent_positive_deviations(self):
        suite = CountDetectorSuite(DetectorConfig(warmup_windows=3, cusum_threshold=5.0))
        for index in range(3):
            suite.update(4, index)
        states = []
        for index in range(3, 8):
            scores, _ = suite.update(8, index)
            states.append(next(score.score for score in scores if score.detector == "cusum"))
        self.assertTrue(all(a < b for a, b in zip(states, states[1:])))
        self.assertGreater(states[-1], 5.0)


if __name__ == "__main__":
    unittest.main()
