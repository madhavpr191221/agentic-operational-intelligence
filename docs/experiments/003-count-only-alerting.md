# Experiment 003: Count-Only Alert Calibration

## Question

Can category-level ticket counts alone identify the seeded payment-volume increase while keeping false alert episodes within the agreed budget? Compare a fixed clean baseline with an EWMA baseline before adding semantic evidence to the investigation stage.

For an intuitive, equation-by-equation explanation of the baselines, count models, detector scores, and results, see [Count-Only Statistical Detection](003-count-only-statistical-analysis.md).

## Detector Contract

- Input: one nonnegative ticket count per category per 30-minute window. Ticket text, logs, issues, changes, and scenario labels are not detector inputs.
- Baseline: either the first 48 clean windows held fixed, or an EWMA initialized from those windows with `lambda=0.2`. Score the current count before updating the EWMA. Freeze EWMA updates after its first alert in a run.
- Candidate scores: Poisson likelihood ratio, negative-binomial likelihood ratio, model-standardized residual, positive CUSUM, and Gamma–Poisson log Bayes factor. They are correlated scores over the same counts; the alert is their calibrated union, not a majority vote.
- Alert episode: consecutive crossing windows for the same category share one statistical episode identifier and track its latest and peak windows. A quiet window ends this grouping; it does not claim the service recovered or close an operational incident.

## Calibration and Evaluation

Calibrate thresholds on seeded `healthy`, `unrelated_issue`, and `resolved_issue` controls. Split a per-window crossing budget across five candidate methods and four categories, then validate the combined per-category episode rate on disjoint seeds. Evaluate both Poisson and negative-binomial arrivals, with and without ticket-event/text materialization; the resulting count streams must match exactly.

Held-out evaluation reports false alert episodes per simulated day across categories and by control, payment detection rate, delay from the severe count-shift onset in windows, and non-payment category alerts during the payment incident. Targets are at most `0.1` false episodes/day and at least `95%` of payment incidents alerted within two windows. Report each arrival model and baseline separately; do not conceal a failed target in a pooled average.

## Held-Out Results

Calibration used seeds `0–499`; evaluation used seeds `1000–1199` (200 seeds per scenario and 1,200 control-days per arrival model). Every configuration met the false-alert target. The detection-delay target passed for Poisson arrivals but **failed for the negative-binomial process**, so the system does not yet meet the 95% goal across both count models.

| Baseline | Arrival model | False episodes/day | Payment alert within 60 min | Mean delay when detected |
|---|---|---:|---:|---:|
| Fixed | Poisson | 0.0375 | 100% | 0.035 windows |
| EWMA (`lambda=0.2`) | Poisson | 0.0700 | 100% | 0.040 windows |
| Fixed | Negative binomial | 0.0625 | 79.5% | 0.853 windows |
| EWMA (`lambda=0.2`) | Negative binomial | 0.0825 | 73.0% | 0.848 windows |

The detailed counts and per-method rates are in [the machine-readable result](003-count-alert-evaluation-results.json). Keep the fixed baseline as the runtime default for now; the EWMA remains an evaluated alternative. Do not loosen thresholds using these held-out seeds. The negative-binomial miss rate is a real unresolved trade-off for a one-hour alert target and should guide a separate tuning experiment.

## Commands

```powershell
uv run python scripts/calibrate_detectors.py --seed-count 500 --target-alerts-per-day 0.1
uv run python scripts/evaluate_detectors.py --heldout-seeds 200 --first-seed 1000 --output docs/experiments/003-count-alert-evaluation-results.json
```

The seeded payment scenario uses clean category rates near Payments 5, Orders 8, Account 4, and Delivery 6 tickets/window. The late latent payment failure raises the expected Payments count to about 31. An unrelated catalog change is placed next to the control windows without changing payment counts. Calibration and held-out seeds must remain disjoint.
