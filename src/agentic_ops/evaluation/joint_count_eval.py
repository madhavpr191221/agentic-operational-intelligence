"""Offline-only joint count detector evaluation. No application wiring."""
from __future__ import annotations

import itertools
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Iterable, Sequence

from agentic_ops.detection.contracts import CountObservation
from agentic_ops.detection.engine import CategoryDetectorSuite

ROOT = Path(__file__).resolve().parents[3]
CATEGORIES = ("payments", "orders", "account", "delivery")
MEANS = (4.5, 8.0, 4.0, 6.0)
WARMUP = 48
HORIZON = 4
STREAM_WINDOWS = 336  # seven scored days of 30-minute windows
SCENARIOS = (
    "healthy_independent", "healthy_common_volume", "shared_sync",
    "shared_delay_1", "shared_delay_2", "shared_delay_3", "shared_delay_4",
    "independent_simultaneous", "independent_delayed", "single_category",
    "beyond_horizon", "overdispersed_independent",
)


@dataclass(frozen=True)
class Fixture:
    """Truth-bearing fixture. Pass only `counts` to scoring functions."""
    counts: tuple[tuple[int, ...], ...]
    truth: dict


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    # Knuth is exact and these means are small.
    limit, product, n = math.exp(-lam), 1.0, 0
    while product > limit:
        n += 1
        product *= rng.random()
    return n - 1


def generate_fixture(scenario: str, seed: int, stream_windows: int = STREAM_WINDOWS) -> Fixture:
    """Generate count-only observations and separate scenario truth metadata."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    rng = random.Random(seed)
    all_counts: list[tuple[int, ...]] = []
    shared_factors = [rng.gammavariate(4.0, 1 / 4.0) for _ in range(WARMUP + stream_windows)]
    onset = WARMUP + 100 + (seed % 100)
    delay = (int(scenario.rsplit("_", 1)[1]) if scenario.startswith("shared_delay_") else
             2 if scenario == "independent_delayed" else 7 if scenario == "beyond_horizon" else 0)
    contributors: tuple[int, ...] = (() if scenario in {"healthy_independent", "healthy_common_volume", "overdispersed_independent"}
                                     else (0,) if scenario == "single_category" else (0, 2))
    lag_truth = (() if not contributors else (0,) if len(contributors) == 1 else (0, delay))
    for t in range(WARMUP + stream_windows):
        rates = list(MEANS)
        common = scenario == "healthy_common_volume"
        if scenario == "overdispersed_independent":
            draws = [rng.gammavariate(4.0, MEANS[i] / 4.0) for i in range(4)]
        else:
            draws = [MEANS[i] * shared_factors[t] if common else MEANS[i] for i in range(4)]

        # One randomly placed planted episode in the scored stream. These labels
        # never enter either candidate's fit or scoring API.
        duration = 3
        # Independent treatment pulses: same marginal impact, different shared-cause truth.
        for idx, lag in zip(contributors, lag_truth):
            if onset + lag <= t < onset + lag + duration:
                draws[idx] *= 3.6
        counts = tuple(_poisson(rng, max(0.01, rate)) for rate in draws)
        all_counts.append(counts)
    truth = {
        "scenario": scenario,
        "onset_index": onset,
        "duration_windows": duration,
        "contributors": [CATEGORIES[i] for i in contributors] if scenario not in {"healthy_independent", "healthy_common_volume", "overdispersed_independent"} else [],
        "contributor_indices": list(contributors) if scenario not in {"healthy_independent", "healthy_common_volume", "overdispersed_independent"} else [],
        "lags": list(lag_truth) if scenario not in {"healthy_independent", "healthy_common_volume", "overdispersed_independent"} else [],
    }
    # No condition/cause labels: this is an association detector fixture.
    return Fixture(tuple(all_counts), truth)


@dataclass(frozen=True)
class Fit:
    means: tuple[float, ...]
    variances: tuple[float, ...]
    correlations: tuple[tuple[float, ...], ...]
    common_shape: float


def fit_null(count_streams: Iterable[Sequence[Sequence[int]]]) -> Fit:
    vectors = [tuple(map(float, row)) for stream in count_streams for row in stream]
    n = len(vectors)
    if n < 2:
        raise ValueError("at least two training vectors required")
    means = tuple(sum(row[c] for row in vectors) / n for c in range(4))
    residuals = [[row[c] - means[c] for c in range(4)] for row in vectors]
    variances = tuple(max(0.2, sum(row[c] ** 2 for row in residuals) / (n - 1)) for c in range(4))
    corr = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        corr[i][i] = 1.0
        for j in range(i):
            cov = sum(row[i] * row[j] for row in residuals) / (n - 1)
            value = max(-0.8, min(0.8, cov / math.sqrt(variances[i] * variances[j])))
            corr[i][j] = corr[j][i] = value
    # Gamma-Poisson shared-volume dispersion estimate from total training counts.
    total_mean = sum(means)
    total_var = sum(variances) + 2 * sum(corr[i][j] * math.sqrt(variances[i] * variances[j]) for i in range(4) for j in range(i))
    shape = max(0.5, min(100.0, total_mean * total_mean / max(0.1, total_var - total_mean)))
    return Fit(means, variances, tuple(tuple(r) for r in corr), shape)


def _subsets(items: Sequence[int]):
    for size in range(2, len(items) + 1):
        yield from itertools.combinations(items, size)


def residual_scan(counts: Sequence[Sequence[int]], fit: Fit, end: int, horizon: int = HORIZON) -> tuple[float, tuple[tuple[int, int], ...]]:
    """Max positive residual-combination score over subsets and lags 0..horizon."""
    best, best_pattern = 0.0, ()
    maxima = []
    for cat in range(4):
        candidates = [(max(0.0, (counts[end-lag][cat] - fit.means[cat]) / math.sqrt(fit.variances[cat])), end-lag)
                      for lag in range(horizon + 1) if end-lag >= 0]
        maxima.append(max(candidates, default=(0.0, end)))
    # For pair scans the objective separates by category: choosing each category's
    # maximum positive residual exactly maximizes its lag combination.
    for a, b in itertools.combinations(range(4), 2):
            selected = ((a, maxima[a][1]), (b, maxima[b][1]))
            z = (maxima[a][0], maxima[b][0])
            rho = fit.correlations[a][b]
            denom = math.sqrt(max(0.5, 2 + 2 * rho))
            score = sum(z) / denom
            if score > best:
                best, best_pattern = score, selected
    return best, best_pattern


def gamma_poisson_scan(counts: Sequence[Sequence[int]], fit: Fit, end: int, horizon: int = HORIZON) -> tuple[float, tuple[tuple[int, int], ...]]:
    """Max shared-rate Gamma-Poisson log Bayes factor over category/lag patterns."""
    best, best_pattern = 0.0, ()
    maxima = []
    for cat in range(4):
        candidates = [(counts[end-lag][cat], end-lag) for lag in range(horizon + 1) if end-lag >= 0]
        maxima.append(max(candidates, default=(0, end)))
    for subset in itertools.combinations(range(4), 2):
            selected = tuple((cat, maxima[cat][1]) for cat in subset)
            observed = sum(counts[idx][cat] for cat, idx in selected)
            expected = sum(fit.means[cat] for cat, _ in selected)
            k = fit.common_shape
            # Integrated shared multiplicative rate G~Gamma(k,k) vs fixed-rate
            # independent Poisson null. Factorials cancel only partially, so keep them.
            log_alt = (math.lgamma(k + observed) - math.lgamma(k) + k * math.log(k)
                       - (k + observed) * math.log(k + expected)
                       + sum(y * math.log(max(1e-12, fit.means[c])) - math.lgamma(y + 1) - fit.means[c]
                             for (c, idx) in selected for y in [counts[idx][c]]))
            log_null = sum(y * math.log(max(1e-12, fit.means[c])) - fit.means[c] - math.lgamma(y + 1)
                           for c, idx in selected for y in [counts[idx][c]])
            score = max(0.0, log_alt - log_null)
            if score > best:
                best, best_pattern = score, selected
    return best, best_pattern


def episode_count(flags: Sequence[bool]) -> int:
    return sum(flag and (i == 0 or not flags[i - 1]) for i, flag in enumerate(flags))


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0: return (0.0, 0.0)
    p = successes / n
    d = 1 + z*z/n
    center = (p + z*z/(2*n))/d
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return max(0.0, center-half), min(1.0, center+half)


def bootstrap_mean_ci(values: Sequence[float], seed: int = 943, draws: int = 1000) -> list[float] | None:
    """Seed-level percentile interval; resampling unit is an independent stream."""
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
    return [means[int(0.025 * (draws - 1))], means[int(0.975 * (draws - 1))]]


def evaluate_stream(counts: Sequence[Sequence[int]], fit: Fit, threshold: float, score_fn) -> dict:
    scores, patterns = [], []
    for end in range(WARMUP, len(counts)):
        score, pattern = score_fn(counts, fit, end)
        scores.append(score)
        patterns.append(pattern)
    flags = [s >= threshold for s in scores]
    return {"flags": flags, "scores": scores, "patterns": patterns, "episodes": episode_count(flags)}


def calibration_quantiles(values: Sequence[float], quantiles: Sequence[float]) -> list[float]:
    ordered = sorted(values)
    return [ordered[min(len(ordered)-1, max(0, math.ceil(q*len(ordered))-1))] for q in quantiles]


def fixture_metadata(fixture: Fixture) -> dict:
    return dict(fixture.truth)


def run_evaluation(seeds_per_partition: int = 500, quick: bool = False) -> dict:
    """Run deterministic partitioned benchmark; `quick` is solely a smoke run."""
    if quick:
        seeds_per_partition = min(seeds_per_partition, 12)
    ranges = {"train": (0, seeds_per_partition), "calibration": (10000, 10000+seeds_per_partition),
              "heldout": (20000, 20000+seeds_per_partition)}
    train_streams = [generate_fixture(s, seed).counts for s in ("healthy_independent", "healthy_common_volume", "overdispersed_independent")
                     for seed in range(*ranges["train"])]
    fitted = fit_null(train_streams)
    methods = {"positive_residual_scan": residual_scan, "shared_gamma_poisson": gamma_poisson_scan}
    q_grid = (0.90, 0.95, 0.975, 0.99, 0.995)
    results = {}
    for name, score_fn in methods.items():
        cal_max = []
        for scenario in ("healthy_independent", "healthy_common_volume", "overdispersed_independent"):
            for seed in range(*ranges["calibration"]):
                stream = generate_fixture(scenario, seed).counts
                ev = evaluate_stream(stream, fitted, math.inf, score_fn)
                cal_max.append(max(ev["scores"], default=0.0))
        thresholds = calibration_quantiles(cal_max, q_grid)
        results[name] = {"thresholds": thresholds, "calibration_max_scores": cal_max, "scenarios": {}}
        for scenario in SCENARIOS:
            reps_by_q = [[] for _ in q_grid]
            for seed in range(*ranges["heldout"]):
                fixture = generate_fixture(scenario, seed)
                ev = evaluate_stream(fixture.counts, fitted, math.inf, score_fn)
                truth = fixture_metadata(fixture)
                onset_rel = truth["onset_index"] - WARMUP
                contributors = set(truth["contributor_indices"])
                for qi, threshold in enumerate(thresholds):
                    flags = [score >= threshold for score in ev["scores"]]
                    has_truth = bool(truth["contributor_indices"])
                    detected = next((i for i, f in enumerate(flags) if f and (i >= onset_rel or not has_truth)), None)
                    found_cats, found_lags = set(), []
                    if detected is not None:
                        for c, idx in ev["patterns"][detected] if ev["patterns"][detected] else ():
                            found_cats.add(c); found_lags.append(WARMUP + detected - idx)
                    reps_by_q[qi].append({"episodes": episode_count(flags), "detected": detected is not None,
                        "delay_windows": None if detected is None or not has_truth else detected-onset_rel,
                        "contributor_recall": len(found_cats & contributors)/len(contributors) if contributors else None,
                        "exact_lag": bool(contributors) and set(found_lags) == set(truth["lags"]),
                        "truth": truth})
            sweep = []
            for quantile, threshold, reps in zip(q_grid, thresholds, reps_by_q):
                n = len(reps); hits = sum(r["detected"] for r in reps)
                lo, hi = wilson(hits, n)
                sweep.append({"calibration_quantile": quantile, "threshold": threshold, "n": n,
                    "detection_rate": hits/n, "detection_rate_95ci": [lo, hi],
                    "mean_episodes_per_stream": mean(r["episodes"] for r in reps),
                    "mean_episodes_95ci": bootstrap_mean_ci([r["episodes"] for r in reps]),
                    "mean_detection_delay_windows": mean([r["delay_windows"] for r in reps if r["delay_windows"] is not None]) if any(r["delay_windows"] is not None for r in reps) else None,
                    "mean_detection_delay_95ci": bootstrap_mean_ci([r["delay_windows"] for r in reps if r["delay_windows"] is not None]),
                    "mean_contributor_recall": mean([r["contributor_recall"] for r in reps if r["contributor_recall"] is not None]) if any(r["contributor_recall"] is not None for r in reps) else None,
                    "exact_lag_rate": mean([float(r["exact_lag"]) for r in reps]) if contributors_any(reps) else None})
            results[name]["scenarios"][scenario] = {"threshold_sweep": sweep}

    # Unmodified per-category detector stack as a reference comparator. A joint
    # reference signal means two distinct categories alert in the same 5-window
    # lookback. Its detector thresholds/configuration are not retuned here.
    ref = {}
    for scenario in SCENARIOS:
        episodes, hits, delays = [], 0, []
        for seed in range(*ranges["heldout"]):
            fixture = generate_fixture(scenario, seed)
            suite = CategoryDetectorSuite("negative_binomial", WARMUP, CATEGORIES)
            alert_history = []
            for index, row in enumerate(fixture.counts):
                observation = CountObservation(index, str(index), str(index+1), dict(zip(CATEGORIES, row)))
                output = suite.update(observation)
                alert_history.append({i for i, c in enumerate(CATEGORIES) if output.categories[c].alerted})
            joint_flags = [len(set.union(set(), *alert_history[max(0, i-HORIZON):i+1])) >= 2 for i in range(len(alert_history))]
            episodes.append(episode_count(joint_flags[WARMUP:]))
            onset = fixture.truth["onset_index"] - WARMUP
            has_truth = bool(fixture.truth["contributor_indices"])
            first = next((i for i, flag in enumerate(joint_flags[WARMUP:]) if flag and (i >= onset or not has_truth)), None)
            if first is not None:
                hits += 1
                if has_truth: delays.append(first-onset)
        lo, hi = wilson(hits, len(episodes))
        ref[scenario] = {"n": len(episodes), "detection_rate": hits/len(episodes), "detection_rate_95ci": [lo, hi],
                         "mean_joint_episodes_per_stream": mean(episodes),
                         "mean_joint_episodes_95ci": bootstrap_mean_ci(episodes),
                         "mean_detection_delay_windows": mean(delays) if delays else None}
    return {"schema_version": 1, "partition_ranges": ranges, "seeds_per_partition": seeds_per_partition,
            "categories": CATEGORIES, "baseline_means": MEANS, "warmup_windows": WARMUP,
            "scored_windows": STREAM_WINDOWS, "lag_horizon": HORIZON, "quantiles": q_grid,
            "fit": asdict(fitted), "methods": results, "existing_per_category_reference": ref,
            "notes": ["No production threshold is selected.", "Thresholds use calibration null maxima; heldout data are not used to tune thresholds.",
                      "Benchmark candidates are not connected to the live detector."]}


def contributors_any(reps: list[dict]) -> bool:
    return any(r["truth"]["contributor_indices"] for r in reps)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds-per-partition", type=int, default=500)
    parser.add_argument("--quick", action="store_true", help="12-seed smoke run, not reportable performance")
    parser.add_argument("--output", type=Path, default=ROOT / "results/multi-category-counts/stage-1-offline-statistical-evaluation.json")
    args = parser.parse_args()
    payload = run_evaluation(args.seeds_per_partition, args.quick)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {args.output} ({payload['seeds_per_partition']} seeds per partition)")


if __name__ == "__main__":
    main()
