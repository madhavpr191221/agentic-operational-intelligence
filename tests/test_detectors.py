import math
import unittest

from agentic_ops.detectors import (
    AlertEpisodeTracker,
    CategoryDetectorSuite,
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

    def test_ewma_scores_before_updating_and_uses_lambda(self):
        suite = CountDetectorSuite(DetectorConfig(
            warmup_windows=4, baseline_mode="ewma", ewma_lambda=0.2,
            poisson_llr_threshold=math.inf, negative_binomial_llr_threshold=math.inf,
            residual_threshold=math.inf, cusum_threshold=math.inf,
            log_bayes_factor_threshold=math.inf,
        ))
        for index in range(4):
            suite.update(5, index)
        scores, alerted = suite.update(10, 4)
        self.assertFalse(alerted)
        self.assertEqual(scores[0].expected, 5.0)
        self.assertEqual(suite.expected, 6.0)
        next_scores, _ = suite.update(6, 5)
        self.assertEqual(next_scores[0].expected, 6.0)

    def test_ewma_freezes_when_combined_alert_fires(self):
        suite = CountDetectorSuite(DetectorConfig(
            warmup_windows=4, baseline_mode="ewma", ewma_lambda=0.2,
            poisson_llr_threshold=math.inf, negative_binomial_llr_threshold=math.inf,
            residual_threshold=1.0, cusum_threshold=math.inf,
            log_bayes_factor_threshold=math.inf,
        ))
        for index in range(4):
            suite.update(5, index)
        _, alerted = suite.update(20, 4)
        self.assertTrue(alerted)
        self.assertTrue(suite.baseline_frozen)
        self.assertEqual(suite.expected, 5.0)
        suite.update(6, 5)
        self.assertEqual(suite.expected, 5.0)

    def test_category_detector_accepts_count_vector_only(self):
        suite = CategoryDetectorSuite("poisson", warmup_windows=2)
        result = suite.update({"payments": 5, "orders": 8, "account": 4, "delivery": 6}, 0)
        self.assertEqual(set(result), {"payments", "orders", "account", "delivery"})
        with self.assertRaisesRegex(ValueError, "Unknown ticket categories"):
            suite.update({"payments": 5, "customer_text": "ignored"}, 1)

    def test_alert_episode_groups_only_consecutive_statistical_crossings(self):
        tracker = AlertEpisodeTracker()
        first = tracker.update("payments", 10, 12, True)
        second = tracker.update("payments", 11, 19, True)
        self.assertEqual(first["episode_id"], second["episode_id"])
        self.assertEqual(second["window_count"], 2)
        self.assertEqual(second["peak_window"], 11)
        self.assertIsNone(tracker.update("payments", 12, 4, False))
        later = tracker.update("payments", 13, 15, True)
        self.assertNotEqual(first["episode_id"], later["episode_id"])


if __name__ == "__main__":
    unittest.main()
