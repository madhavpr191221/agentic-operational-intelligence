# Experiment 001: Initial Detector Evaluation

## Setup

- Generator: 72 simulated hours in 30-minute ticket windows; first 24 hours are baseline warmup.
- Calibration: seeds 0-99 across healthy, unrelated-issue, and resolved-issue controls.
- Evaluation: seeds 100-149 across those controls and the unresolved payment-failure scenario.
- Both Poisson and negative-binomial ticket-arrival generators were evaluated.
- The Bayesian comparator used a fixed Gamma alternative with shape 2 and mean twice the baseline rate.

## Held-Out Results

| Arrival model | Positive detection within 2 windows | Combined control false alert episodes/day |
|---|---:|---:|
| Poisson | 100% | 0.06 |
| Negative binomial | 100% | 0.01 |

Each individual candidate method detected all 50 positive runs within two windows. The combined alert rule met the initial target of at most 0.1 false alert episodes per simulated day for both generator assumptions.

## Interpretation and Limits

The planted ticket increase is intentionally strong. These results show that the vertical slice behaves as designed; they do not establish realistic field performance. Calibration and evaluation use the same simulator family. Later experiments should add weaker and gradual shifts, seasonal baselines, parameter variation, and out-of-family controls before making broader claims.

Recreate thresholds with `python scripts/calibrate_detectors.py`; evaluate held-out seeds with `python scripts/evaluate_detectors.py`. The saved threshold bundle is specific to the current generator and must be regenerated when scenario mechanics or detector code changes.
