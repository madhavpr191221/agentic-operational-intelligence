# Anomaly Detection Experiments — Current Architecture

> **Reconstruction note**
>
> This document was reconstructed from the architecture screenshots shared on 18 November 2026. It is intended to preserve the **current system shown in those screenshots**, not the proposed future agentic-RCA system. Where text was not fully visible, the document either omits it or marks the reconstruction as approximate rather than silently inventing detail.

---

## 1. Scope

The repository is the **core scoring engine only**.

The screenshots describe it as a pure-Python pipeline that reads and writes CSV/JSON data on disk and has no server, database, or UI in the core path.

A separate full-stack application exists outside this pipeline. The screenshots describe that application as having a React frontend / Node.js backend and database/deployment components. That application is explicitly excluded from the core pipeline described here.

A `src/analytics/` subsystem also exists in the repository, but it is **not part of the scoring pipeline below**. See Section 9.

At a high level:

- count metrics are scored using an EWMA baseline plus a Poisson rate-shift Log Bayes Factor style score;
- rate metrics are scored with a z-score for spikes and CUSUM for persistent drift;
- synthetic ground truth is used to backtest detections.

---

## 2. High-Level Architecture

```text
generate_synthetic_data.py
    |
    | deterministic seed=42 synthetic CX data generator
    | plants 4 known anomalies for validation
    |
    v
data/raw/*.csv
    |
    | 8 raw CSVs:
    | cx_case
    | cx_case_overall_info
    | cx_survey_response
    | cx_email
    | cx_live_chat
    | cx_chat_bot
    | kpi_chatbot
    | kpi_web_chat
    |
    v
aggregate_metrics.py
    |
    | pure mechanical rollup
    | ZERO awareness of anomalies
    |
    v
data/aggregated/
    |-- 30min_volume.csv
    |-- daily_rates.csv
    `-- daily_time_stats.csv

          +--------------------------+--------------------------+
          |                          |                          |
          v                          v                          v
run_30min_scoring.py        run_daily_rate_scoring.py   run_batch_scoring.py
(Log BF on counts)          (z-score + CUSUM rates)     (reads data/raw
                                                        directly; cross-type
                                                        itemset mining)
          |                          |                          |
          v                          v                          v
results/30min_              results/daily_rate_          results/batch_
anomalies.json              anomalies.json               output.json
          |                          |
          +-------------+------------+
                        |
                        v
              src/validation/backtest.py
                        |
                        | matches detections against
                        | known planted anomalies
                        |
                        v
               precision / recall / lag
```

### Key architectural fact

The three `run_*_scoring.py` scripts are shown as **independent and self-contained**.

They do not share one common statistical library. Each script independently re-derives or contains its own copies of some combination of:

- EWMA baseline,
- Log Bayes Factor,
- z-score,
- CUSUM.

This means a correctness fix to a statistical formula may need to be manually ported to multiple sibling scripts.

---

## 3. Repository Map

The following tree is reconstructed from the paths visible in the screenshots.

```text
anomaly-detection-experiments/
|
|-- src/
|   |-- aggregate_metrics.py
|   |
|   |-- utils/
|   |   `-- generate_synthetic_data.py
|   |
|   |-- scoring/
|   |   |-- run_30min_scoring.py
|   |   |-- run_daily_rate_scoring.py
|   |   `-- run_batch_scoring.py
|   |
|   |-- validation/
|   |   `-- backtest.py
|   |
|   `-- analytics/
|       |-- apriori.py
|       |-- scoring.py
|       |-- clustering.py
|       `-- models.py
|
|-- tests/
|   `-- test_scoring_granularity.py
|
|-- data/
|   |-- raw/
|   |-- aggregated/
|   `-- ground_truth/
|       `-- planted_anomalies.json
|
|-- results/
|   |-- 30min_anomalies.json
|   |-- daily_rate_anomalies.json
|   |-- batch_output.json
|   `-- validation/
|       `-- backtest_report.json
|
`-- docs/
    `-- ARCHITECTURE.md
```

The screenshots explicitly warn that `src/analytics/` should not be confused with the active scoring path.

---

## 4. Data Model

### 4.1 Raw CSVs

The generator writes eight raw CSVs.

The architecture notes state that `aggregate_metrics.py` consumes only a subset of them; the remaining files exist for realism/completeness in the synthetic dataset.

| File | Grain | Visible key columns / notes |
|---|---|---|
| `cx_case.csv` | 1 row / support case | `caseid`, `createddate`, `category__c`, `subcategory__c`, `detail_category__c`, `origin`, `subject`, `description` |
| `cx_case_overall_info.csv` | 1 row / case | `id`, `device_type`, `origin`, category/subcategory/detail-category fields, `csat_combined`, `Average TTFR`, `Time to Closure Hours` |
| `cx_survey_response.csv` | ~10% of cases | `caseid`, `survey_createddate`, resolution-satisfaction field, customer-comments field |
| `cx_email.csv` | 1–4 rows / case | `caseid`, `emailid`, `email_createddate`, `email_type`, `subject` |
| `cx_live_chat.csv` | subset of cases | `livechat_id`, `caseid`, `starttime`, category/subcategory fields, transcript |
| `cx_chat_bot.csv` | — | Written, but described as unused downstream in this repo's scoring pipeline |
| `kpi_chatbot.csv` | 1 row / chatbot session | `session_id`, `session_start_time`, `self_serviced_vs_escalated`, `session_duration_seconds`, `first_response_time_seconds`, `subsequent_response_time_seconds`, message-count fields |
| `kpi_web_chat.csv` | 1 row / case | `case_id`, `created_date`, `service_channel`, `csat`, `ttfr_seconds`, `handle_time_minutes`, `time_to_closure_hours` |

### 4.2 Aggregated CSVs

#### `30min_volume.csv`

Visible fields:

```text
date
time_window
category
subcategory
channel
case_count
session_count
```

It is produced via a four-level rollup.

Every case increments counters at multiple aggregation levels, including combinations corresponding to:

```text
(category, subcategory, channel)
(category, subcategory, ALL)
(category, ALL, ALL)
(ALL, ALL, ALL)
```

The architecture notes state that `session_count` has no category breakdown in its raw source, so it is populated only on fully rolled-up rows where category is `ALL`.

#### `daily_rates.csv`

Visible fields include:

```text
date
category
subcategory
total_sessions
self_service_count
escalation_count
abandoned_count
self_service_rate
escalation_rate
abandoned_rate
```

Important limitation documented in the screenshots:

- aggregation is effectively date-level;
- category/subcategory are hard-coded to `ALL`;
- there is no real per-category rate breakdown in this file.

#### `daily_time_stats.csv`

This is a long/tidy summary table with:

```text
date
channel
metric
```

plus a 17-field statistical summary produced by `compute_stats()`.

Visible statistics include values such as:

```text
count
min
max
mean
std
cv
p10
p20
p25
p30
p40
p50
p75
p90
p99
iqr
```

The screenshots show metrics for web-chat / chatbot timing and session behavior, including examples such as:

```text
ttfr
handle_time
time_to_closure
first_response_time
subsequent_response_time
session_duration
user_message_count
```

This aggregation is also date-grain and does not carry a category breakdown.

### Consequence of the aggregation design

The architecture explicitly notes that a **category-specific anomaly** may only be visible in the segmented `30min_volume.csv`, while a **global anomaly** can still be visible in `daily_rates.csv`.

---

## 4.3 Result JSON Schemas

The screenshots show the following output shapes.

### `30min_anomalies.json`

Produced by `run_30min_scoring.py`.

```json
{
  "parameters": {
    "input": "...",
    "anomaly_threshold": "...",
    "min_observed": "...",
    "burn_in_days": "...",
    "metrics_scored": "..."
  },
  "summary": {
    "date_range": "...",
    "total_days": "...",
    "days_with_detections": "...",
    "total_detections": "...",
    "segments_with_detections": "..."
  },
  "anomalies": [
    {
      "metric": "...",
      "date": "...",
      "time_window": "...",
      "segment": "...",
      "observed": "...",
      "expected": "...",
      "ratio": "...",
      "log_bf": "...",
      "z_score": "..."
    }
  ]
}
```

### `daily_rate_anomalies.json`

Produced by `run_daily_rate_scoring.py`.

```json
{
  "parameters": {
    "input": "...",
    "zscore_threshold": "...",
    "cusum_threshold": "...",
    "burn_in_days": "..."
  },
  "summary": {
    "date_range": "...",
    "total_days": "...",
    "days_with_detections": "...",
    "total_detections": "...",
    "detection_breakdown": "..."
  },
  "anomalies": [
    {
      "metric": "...",
      "date": "...",
      "segment": "...",
      "observed": "...",
      "expected": "...",
      "std": "...",
      "z_score": "...",
      "cusum": "...",
      "detection": "..."
    }
  ]
}
```

### `batch_output.json`

Produced by `run_batch_scoring.py`.

```json
{
  "parameters": {
    "input_dir": "...",
    "max_itemset_size": "...",
    "decay_half_life": "...",
    "anomaly_threshold": "...",
    "burn_in_days": "..."
  },
  "summary": {
    "date_range": "...",
    "total_days": "...",
    "days_scored": "...",
    "days_with_anomalies": "...",
    "total_anomalies": "..."
  },
  "results": [
    {
      "date": "...",
      "anomaly_count": "..."
    }
  ],
  "anomalies": [
    {
      "itemset": "...",
      "score": "...",
      "log_bf": "...",
      "cusum_score": "...",
      "observed": "...",
      "expected": "...",
      "expected_std": "...",
      "is_new": "..."
    }
  ]
}
```

Some key names in the batch schema are partially obscured in the source photograph; the structure above follows the visible fields.

---

## 5. Synthetic Data Generation Deep-Dive

File shown:

```text
src/utils/generate_synthetic_data.py
```

The screenshot identifies the file as approximately 929 lines.

Generation is fully deterministic:

```python
random.seed(42)
```

### 5.1 Baseline volume and distributions

Visible baseline constants:

```text
CASES_PER_DAY_BASELINE = 2500
CHATBOT_SESSIONS_PER_DAY_BASELINE = 6000
```

Category selection uses weights approximately:

```text
Account       0.30
Payments      0.25
Drops         0.20
Broadcasting  0.15
General       0.10
```

Subcategory selection is uniform within the selected category.

The generator also includes:

- hour-of-day weights, with peaks around mid-morning/afternoon and a trough overnight;
- day-of-week multipliers, with weekend values around `0.75–0.8x`;
- daily volume noise.

The visible daily-volume construction is approximately:

```python
get_volume_multiplier(date)
    = DOW_MULTIPLIERS[weekday] * clamp(gauss(1.0, 0.1), 0.7, 1.3)
```

Time-based metrics are generated from log-normal distributions.

Visible examples include:

```text
TTFR
handle time
session duration
bot first-response time
subsequent-response time
```

The screenshot explicitly notes that `time_to_closure` is uniform over roughly 1–72 hours rather than log-normal.

Chatbot session transcripts come from a canned template bank keyed by category/subcategory, with roughly 30–120 second gaps between turns.

---

## 5.2 Planted Anomalies — Exact Injection Mechanics

The screenshots show four planted anomalies, each using a different mechanism.

### 1. Account/Login case-volume spike — 25x

Window:

```text
15 Dec 2025
14:00–17:00
```

Mechanism:

- additive;
- window-overlap-scaled;
- baseline in the window is derived from the category's share of daily volume and the fraction of the day covered by the window;
- extra cases are generated inside the exact time window;
- timestamps are distributed uniformly in the window;
- the change is a hard step, not a ramp.

The visible formula is conceptually:

```python
extra_cases = int(baseline_in_window * (multiplier - 1))
```

### 2. Session-volume spike — 2x

Window:

```text
15 Apr 2026
full day
```

Mechanism:

- direct day-level multiplier;
- applied to the entire day's chatbot-session total before the per-session generation loop;
- not category-scoped.

Conceptually:

```python
daily_count = int(daily_count * multiplier)
```

### 3. `escalation_rate` drift — +0.45

Window:

```text
1–7 Feb 2026
```

Mechanism:

- outcome-weight shift;
- applied globally.

The architecture notes an important implementation mismatch:

- the anomaly definition specifies a category/subcategory such as Payments/Refunds;
- those fields are not actually checked for this rate mutation;
- therefore the drift is applied to all sessions regardless of category.

Visible outcome weights during the anomaly are approximately:

```text
self_service = 0.45
escalated    = 0.45
abandoned    = 0.10
```

The change is described as a flat step, with no ramp.

### 4. Drops TTFR degradation — 1.8x

Window:

```text
20–22 Mar 2026
```

Mechanism:

- per-record multiplier;
- applied at generation time for matching cases;
- generated timing value is multiplied by `1.8`.

Conceptually:

```python
lognormal_sample(mu, sigma, multiplier=1.8)
```

---

## 5.3 Dead Code

The screenshots explicitly identify two functions in `generate_synthetic_data.py` as defined but never called from `main()`:

```text
generate_kpi_web_chat()
generate_kpi_chatbot()
```

They are described as 30-minute-window pre-aggregators that were superseded by the separate `aggregate_metrics.py` path.

---

## 6. Aggregation Pipeline Deep-Dive

File:

```text
src/aggregate_metrics.py
```

The screenshot identifies it as approximately 383 lines.

The central design principle is explicit:

> `aggregate_metrics.py` has **zero awareness of anomalies**.

It is a mechanical transformation of whatever is present in the raw synthetic data.

### `aggregate_30min_volume()`

Builds separate counters per case in one pass to create the category/subcategory/channel rollup hierarchy.

The screenshots emphasize that this is done directly rather than by first computing fine-grained aggregates and summing them later.

### `aggregate_daily_rates()`

Groups strictly by date.

There is no category dimension in the actual rate aggregation.

### `aggregate_daily_time_stats()`

Also groups strictly by date.

### `compute_stats(values)`

A shared helper produces a 17-field statistical summary.

Percentiles are computed using linear interpolation.

---

## 7. Scoring Engines

## 7.1 Shared Statistical Foundations

The architecture notes say the common mathematics is **duplicated independently in all three scoring scripts**.

### EWMA baseline

The documented weight is:

```text
w_i = exp(-ln(2) / half_life * age_i)
```

Weighted mean:

```text
mean = sum(w_i * x_i) / sum(w_i)
```

Weighted variance:

```text
var = sum(w_i * (x_i - mean)^2) / sum(w_i)
```

Standard deviation:

```text
std = sqrt(var)
```

### Important normalization pitfall

The architecture explicitly emphasizes:

> Divide by the **sum of weights**, not by raw day count.

For a 30-day window with `half_life = 7`, the screenshot gives an example effective weight sum of roughly:

```text
sum(w_i) ~= 14.4
```

rather than 30.

Dividing by raw day count would substantially underestimate the expected value and create many false positives.

The architecture notes state that this was documented as a pitfall from the beginning rather than being a later bug in the scoring formula.

---

### Log Bayes Factor / Poisson rate-shift score

For count metrics:

```text
lambda0 = max(expected, 0.1)
delta   = max(lambda0, 3.0)
lambda1 = lambda0 + delta

log_bf = observed * log(lambda1 / lambda0) - delta
```

The visible evidence interpretation table is:

| `log_bf` | Evidence | Approx. odds |
|---:|---|---:|
| `< 1` | noise | `< 3:1` |
| `1–3` | substantial | `3:1–20:1` |
| `3–5` | strong | `20:1–150:1` |
| `5–10` | very strong | `150:1–22,000:1` |
| `> 10` | decisive | `> 22,000:1` |

---

### Z-score

For rate metrics:

```text
z = (observed - expected) / max(std, floor)
```

The screenshots note that the floor differs slightly by script.

They also describe `run_batch_scoring.py` as using a Poisson-inspired pseudo-standard-deviation for sufficiently new itemsets, while rate scripts use a small fixed floor.

---

### CUSUM with adaptive allowance

The visible implementation is:

```text
ema_alpha = 1 - exp(-ln(2) / ema_halflife)
```

with a default half-life shown as approximately seven days.

Adaptive allowance:

```text
k_t = k_{t-1} + alpha * (z - k_{t-1})
```

CUSUM state:

```text
S_t = max(0, decay * S_{t-1} + z - k_t)
```

with:

```text
decay = 0.95
```

Interpretation given in the architecture:

- `k_t` is an EMA of recent z-scores rather than a fixed allowance;
- persistent baseline noise causes the allowance to follow the background and keeps CUSUM relatively flat;
- a genuinely new signal where `z >> k_t` accumulates rapidly;
- once the signal resolves and `z << k_t`, the state drains rapidly.

---

## 7.2 `run_30min_scoring.py`

Inputs:

```text
data/aggregated/30min_volume.csv
```

It scores count metrics such as:

```text
case_count
session_count
```

The baseline is **same-time-of-day**.

A given 30-minute window is compared against the corresponding window on prior days rather than against adjacent windows.

Detection is gated by Log Bayes Factor against an anomaly threshold.

The screenshot shows:

- CLI default around `3.0`;
- documentation mentioning `5.0` as a “balanced” tuning recommendation.

A z-score is also computed and reported, but the architecture notes say it is not itself a gating condition for this script.

Output:

```text
results/30min_anomalies.json
```

---

## 7.3 `run_daily_rate_scoring.py`

Input:

```text
data/aggregated/daily_rates.csv
```

Visible metrics include:

```text
escalation_rate
self_service_rate
abandoned_rate
```

The baseline is a preceding-days EWMA rather than a same-time-of-day model because the data are daily-grain.

Detection fires when either:

```text
abs(z_score) >= zscore_threshold
```

or:

```text
cusum >= cusum_threshold
```

The screenshot shows defaults approximately:

```text
z-score threshold = 3.0
CUSUM threshold   = 15.0
```

The detection record indicates whether the trigger was:

```text
zscore
cusum
both
```

CUSUM/EWMA state is carried forward during burn-in so the model is warm when reporting begins; detections are simply not reported during burn-in.

Output:

```text
results/daily_rate_anomalies.json
```

---

## 7.4 `run_batch_scoring.py`

This is described as the most complex of the three scoring scripts.

Unlike the other two paths, it reads:

```text
data/raw/cx_case.csv
```

directly, bypassing the aggregated CSVs.

It performs **typed cross-type itemset mining**.

The architecture states that this implementation is structurally similar to `src/analytics/apriori.py` but is an independent implementation rather than a shared module.

Output:

```text
results/batch_output.json
```

---

## 7.5 Duplication Across Scoring Scripts

The architecture explicitly warns:

> The scoring scripts re-derive the EWMA / Log-BF / z-score / CUSUM mathematics independently.

Therefore a detection-formula bug may require fixes in multiple places.

This is one of the central maintainability limitations of the current implementation.

---

## 8. Validation Framework

File:

```text
src/validation/backtest.py
```

The screenshot identifies it as approximately 278 lines.

The backtester is generic over detection JSON files that follow an:

```json
{"anomalies": [...]}
```

shape and a ground-truth file containing planted anomaly definitions.

### Matching

The visible matching rules include:

- detection timestamp must fall within the ground-truth window;
- metric name must match exactly;
- segment matching supports `ALL` as a wildcard;
- the observed/expected relationship must be compatible with the expected direction such as increase/decrease.

### Metrics

The framework computes:

```text
recall    = detected_ground_truth_count / ground_truth_count
precision = matched_detections / total_detections
```

It also computes average detection lag from the start of the ground-truth window to the first matching detection.

The framework reports the top 20 highest-scoring unmatched detections as likely false positives for inspection.

---

## 9. `src/analytics/` Subsystem — Not Part of the Pipeline Above

The screenshots explicitly call this out because it is easy to confuse with the active scoring path.

Visible files include:

```text
src/analytics/apriori.py
src/analytics/scoring.py
src/analytics/clustering.py
src/analytics/models.py
```

The subsystem is described as a typed cross-type itemset-mining engine for **LLM-extracted help-desk ticket data**, including object dimensions such as:

```text
browser
os
device
platform-area
```

with confidence scores.

The source document also references:

- a FastAPI service;
- endpoints such as `/api/mining/run` and `/api/mining/batch`;
- DynamoDB-backed storage such as a `daily_alerts` table;
- a separate full-stack application.

The architecture is explicit that this subsystem is **not wired into** the active `src/scoring/run_*.py` pipeline.

### Dependency mismatch

The screenshots note that `models.py` imports `pydantic`, but that dependency is not declared consistently in the environment shown, making the subsystem non-importable as-is.

---

## 10. Testing Status

The screenshots describe:

```text
tests/test_scoring_granularity.py
```

as a **plain script, not a real pytest suite**.

It has no normal test-runner assertion structure; it prints/returns results and can be run directly:

```bash
python -m tests.test_scoring_granularity
```

It validates that `src/analytics/scoring.py` daily vs 30-minute granularity modes:

- correctly detect a planted spike; and
- correctly avoid flagging a non-spike window.

### Important testing gap

The architecture states that there is no direct unit-test coverage for the actual production/core scripts:

```text
run_30min_scoring.py
run_daily_rate_scoring.py
run_batch_scoring.py
aggregate_metrics.py
generate_synthetic_data.py
```

Confidence in these paths currently comes primarily from the end-to-end backtest against planted anomalies rather than direct unit tests.

---

## 11. Evolution / Git History

The screenshots list six commits, oldest to newest:

```text
0f38bb1  Initial commit: Anomaly detection scoring engine
30ea9bb  Add rate metric scoring and fix escalation anomaly injection
25d7fd4  Fix case_volume anomaly injection and update documentation
a15905c  Add 30-minute granularity support to scoring.py
b59599d  Add generic backtest validation framework
05061c0  Add rate scoring functions to scoring.py
```

### Narrative captured in the architecture

The document makes two important historical points.

#### EWMA normalization was not a later scoring bug

The weight-normalized EWMA formula was documented as correct from the initial implementation.

The repository includes comments contrasting the correct normalization against the incorrect divide-by-day-count approach.

#### The known early correctness bugs were in synthetic anomaly injection

The screenshots state that two genuine correctness issues occurred in the generator/anomaly-injection logic rather than in the statistical scoring mathematics.

The commit history specifically records fixes for:

- escalation anomaly injection; and
- case-volume anomaly injection.

The exact full narrative for those fixes is partially obscured in the source photographs, so it is not expanded further here.

---

## 12. Known Limitations / Architectural Warnings

The screenshots list several warnings.

### 12.1 Dependency / version mismatch

`pyproject.toml` / `uv.lock` declare packages such as:

```text
numpy
pandas
polars
matplotlib
```

and a newer Python requirement, while the active core pipeline shown in the architecture uses standard-library modules such as:

```text
csv
json
math
statistics
argparse
collections
pathlib
datetime
```

The document warns not to infer actual runtime needs purely from the project metadata.

### 12.2 Segment-key format is not uniform

The architecture notes that segment strings differ between paths.

Examples:

```text
30-minute volume:
category|subcategory|channel

daily-rate:
category|subcategory
```

`ALL` is used as a rollup/wildcard.

Any code parsing segment strings, including validation matching, must handle the differing shapes.

### 12.3 No shared statistical module

The three scoring scripts duplicate formula logic.

Correctness changes may need to be manually ported to multiple files.

### 12.4 Category-blind rate/time-stat aggregation

Rate and time-stat aggregation loses category-level segmentation.

Therefore category-specific rate or response-time anomalies may be invisible to the current daily-rate/time-stat scoring path even if they exist in the raw data.

### 12.5 `src/analytics/` looks relevant but is not on the active path

Do not change `src/analytics/` expecting it automatically to alter `src/scoring/` output.

The subsystem also has dependency issues in the environment shown.

### 12.6 Dead generator functions

The screenshots call out:

```text
generate_kpi_web_chat()
generate_kpi_chatbot()
```

as defined but unused by `main()`.

---

## 13. Appendix

## 13.1 CLI Reference

### Synthetic data generation

```bash
python src/utils/generate_synthetic_data.py \
  --start YYYY-MM-DD \
  --end YYYY-MM-DD \
  --output data/raw
```

### Aggregation

```bash
python src/aggregate_metrics.py \
  --input data/raw \
  --output data/aggregated
```

### 30-minute volume scoring

The screenshot shows a command of the following form:

```bash
python -m src.scoring.run_30min_scoring \
  --input data/aggregated/30min_volume.csv \
  --output results/30min_anomalies.json \
  --anomaly-threshold 3.0 \
  --min-observed 5 \
  --burn-in-days 14
```

### Backtest against ground truth

```bash
python -m src.validation.backtest \
  --ground-truth data/ground_truth/planted_anomalies.json \
  --detections results/30min_anomalies.json results/daily_rate_anomalies.json \
  --output results/validation/backtest_report.json
```

### Scoring-granularity sanity script

```bash
python -m tests.test_scoring_granularity
```

---

## 13.2 Formula Reference

| Formula | Expression |
|---|---|
| EWMA weight | `w_i = exp(-ln(2) / half_life * age_i)` |
| EWMA mean | `mean = sum(w_i * x_i) / sum(w_i)` |
| EWMA variance | `var = sum(w_i * (x_i - mean)^2) / sum(w_i)` |
| Log Bayes Factor | `log_bf = observed * log((lambda0 + delta) / lambda0) - delta`, `lambda0=max(expected,0.1)`, `delta=max(lambda0,3.0)` |
| Z-score | `z = (observed - expected) / max(std, floor)` |
| CUSUM allowance (EMA) | `k_t = k_{t-1} + alpha * (z - k_{t-1})`, `alpha = 1 - exp(-ln(2) / ema_halflife)` |
| CUSUM | `S_t = max(0, decay * S_{t-1} + z - k_t)`, `decay = 0.95` |

---

# Current-State Summary

The current repository is best understood as a **deterministic synthetic-data + statistical anomaly-detection + backtesting system**.

Its strongest architectural properties are:

- explicit planted ground truth;
- anomaly-blind aggregation;
- metric-aware detection rather than one generic anomaly model;
- interpretable statistical scoring;
- same-time-of-day baselines for intraday counts;
- sequential drift detection for daily rates;
- end-to-end backtesting.

Its most important current limitations are:

- duplicated statistical logic;
- inconsistent segment representation;
- category information lost in some aggregate paths;
- limited direct unit-test coverage;
- legacy/unwired `src/analytics/` code that can be confused with the active pipeline;
- synthetic injection semantics that have historically required correctness fixes.

This document intentionally stops at the current system shown in the screenshots. The future Agentic RCA architecture should be documented separately so that implemented and proposed functionality are never conflated.
