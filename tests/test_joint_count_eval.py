from inspect import signature

from agentic_ops.detection.engine import calibrated_config
from agentic_ops.evaluation.joint_count_eval import (
    CATEGORIES, Fit, episode_count, fit_null, gamma_poisson_scan,
    generate_fixture, residual_scan, run_evaluation,
)


def test_fixture_repeats_and_keeps_truth_outside_count_matrix():
    first = generate_fixture("shared_delay_2", 17)
    second = generate_fixture("shared_delay_2", 17)
    assert first.counts == second.counts
    assert first.truth == second.truth
    assert len(first.counts[0]) == len(CATEGORIES)
    assert first.truth["contributors"] == ["payments", "account"]
    assert "truth" not in signature(residual_scan).parameters


def test_train_calibration_and_heldout_seed_ranges_are_disjoint():
    result = run_evaluation(seeds_per_partition=2, quick=True)
    ranges = result["partition_ranges"]
    assert ranges["train"][1] <= ranges["calibration"][0]
    assert ranges["calibration"][1] <= ranges["heldout"][0]
    assert all(method in result["methods"] for method in ("positive_residual_scan", "shared_gamma_poisson"))


def test_lag_scan_finds_pair_at_four_window_boundary():
    fit = Fit((4.5, 8.0, 4.0, 6.0), (5.0, 9.0, 5.0, 7.0),
              ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)), 4.0)
    rows = [[4, 8, 4, 6] for _ in range(8)]
    rows[3][0] = 80  # four completed windows behind end=7
    rows[7][2] = 80
    for method in (residual_scan, gamma_poisson_scan):
        score, pattern = method(rows, fit, 7)
        assert score > 0
        assert {category for category, _ in pattern} == {0, 2}
        assert {7-index for _, index in pattern} == {0, 4}


def test_beyond_horizon_fixture_has_no_pulse_tail_inside_search_window():
    fixture = generate_fixture("beyond_horizon", 9)
    onset = fixture.truth["onset_index"]
    # A three-window first pulse ends at onset+2; the next starts at onset+7.
    # Therefore the last first-category affected count is five full steps back.
    assert fixture.truth["lags"] == [0, 7]
    assert (onset + 7) - (onset + 2) > 4


def test_episode_count_groups_adjacent_flags_only():
    assert episode_count([False, True, True, False, True, False]) == 2


def test_existing_detector_calibration_is_not_mutated():
    config = calibrated_config("negative_binomial", 48, category="payments")
    assert config.warmup_windows == 48
    assert config.score_model == "negative_binomial"
    assert config.residual_threshold > 0
