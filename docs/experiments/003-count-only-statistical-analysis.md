# Count-Only Statistical Detection: An Intuitive and Mathematical Guide

## The Short Version

The detector watches one number per category every 30 minutes: how many support tickets arrived. It does not read ticket text or inspect logs, issues, or changes. In the seeded payment scenario, Payments rises from about 5 tickets per window to about 31.

We tested two definitions of “usual” and two models for count variability. Every setup stayed below the false-alert budget of 0.1 episodes per simulated day. Poisson setups detected the planted payment spike within one hour in 100% of 200 runs. Negative-binomial setups did so in 79.5% of runs with a fixed baseline and 73% with EWMA. That misses the 95% timeliness goal for the negative-binomial cases.

## What Counts as “Usual”?

Let $x_t$ be the number of tickets in one category during 30-minute window $t$. The first 48 windows (24 hours) initialize a clean baseline. The detector compares each later window with an expected count $\hat{\mu}_t$.

### Fixed baseline

The fixed baseline is the average of those 48 clean windows:

$$
\hat{\mu}_{\text{fixed}} = \frac{1}{48}\sum_{t=1}^{48} x_t
$$

It is easy to interpret and does not drift when later counts change. It can become stale if genuine normal behavior changes over time.

### EWMA baseline

The exponentially weighted moving average (EWMA) updates the estimate gradually. With $\lambda=0.2$:

$$
\hat{\mu}_{t+1} = 0.2x_t + 0.8\hat{\mu}_t
$$

Recent windows influence the estimate more than older ones. The detector scores window $t$ against the estimate from before that window, then updates it. Once an alert fires, EWMA updates freeze so the detected spike cannot quickly become the new normal.

The fixed baseline is the runtime default. EWMA is an evaluated alternative.

## What Do Poisson and Negative Binomial Mean?

These are assumptions about how ticket counts naturally vary around their expected value $\mu$; they do not explain why a spike happened.

- **Poisson:** assumes the variance is about equal to the mean, $\operatorname{Var}(X)=\mu$. Counts are relatively regular when the expected rate is fixed.
- **Negative binomial (NB):** allows extra variability, $\operatorname{Var}(X)=\mu+\mu^2/r$, where $r$ controls the amount of extra spread. Smaller $r$ means noisier counts and more large values can occur even without an incident.

That extra spread makes a high count less surprising under NB. It can reduce false alarms, but it can also make a real spike harder to distinguish from normal variation. This matches the weaker NB timeliness results in this experiment; it is an interpretation of the measured comparison, not proof that this is the only cause.

The simulator generates both Poisson and NB arrivals. For each window, the detector also computes both likelihood-ratio scores. The selected arrival model determines which variance is used for the standardized residual and CUSUM.

## How Does a Count Become an Alert?

Five related scores are calculated from the same count. Their thresholds are calibrated on healthy, unrelated-change, and resolved-issue simulations. An alert fires if **any one** score crosses its threshold. This is a calibrated union of signals, not a vote; the scores are correlated because they all inspect the same count.

### 1. Standardized residual (Z-score)

The residual measures how far the observed count is above expectation in units of typical variation:

$$
Z_t = \frac{x_t-\hat{\mu}_t}{\sqrt{\operatorname{Var}(X_t)}}
$$

The detector considers upward deviations. Under Poisson, the variance is $\hat{\mu}_t$; under NB it is $\hat{\mu}_t+\hat{\mu}_t^2/r$. A Z-score of 3 means the count is three modeled standard deviations above expectation. The actual alert cutoff is calibrated empirically, not assumed to be a universal “3-sigma” rule.

### 2. CUSUM

CUSUM accumulates repeated upward deviations, so several moderately high windows can add up to evidence of a sustained shift:

$$
S_t = \max\left(0, S_{t-1}+Z_t-0.5\right)
$$

The $0.5$ allowance means small deviations do not accumulate indefinitely. A reset to zero discards evidence from earlier downward or small deviations. An alert occurs when $S_t$ crosses its calibrated threshold.

### 3. Poisson likelihood ratio

This asks how much better a higher Poisson rate fits the observed count than the baseline rate. For $x_t>\hat{\mu}_t$:

$$
\operatorname{LLR}_{\text{Pois}} = x_t\log\left(\frac{x_t}{\hat{\mu}_t}\right) - (x_t-\hat{\mu}_t)
$$

For counts at or below expectation, the upward score is zero. A larger value means the count is less compatible with the baseline rate.

### 4. Negative-binomial likelihood ratio

This is the same comparison under an NB count model: compare the baseline mean with a best-fitting higher mean for this observation. For $x_t>\hat{\mu}_t$:

$$
\operatorname{LLR}_{\text{NB}} = \log f_{\text{NB}}(x_t; x_t,r) - \log f_{\text{NB}}(x_t; \hat{\mu}_t,r)
$$

Here $f_{\text{NB}}(x;m,r)$ is the NB probability of count $x$ with mean $m$ and shape $r$. The score allows more background variation than the Poisson score, so the same spike can produce weaker evidence.

### 5. Gamma-Poisson Bayes factor

The Bayes factor compares a baseline-rate explanation with an alternative that allows a higher rate:

$$
\operatorname{BF}_{10} = \frac{p(x_t\mid H_1)}{p(x_t\mid H_0)}
$$

Here, $H_0$ uses the baseline rate. Under $H_1$, the rate has a Gamma prior with shape 2 and prior mean twice the baseline rate. The detector thresholds the **log** Bayes factor. This score depends on the stated prior; it is evidence favoring one rate model over another, not a probability that a particular service caused the spike.

## What Did the Evaluation Find?

Thresholds were calibrated on seeds 0-499. Evaluation used separate seeds 1000-1199: 200 simulated payment incidents and 1,200 control-days for each arrival-model/baseline configuration. A 30-minute window is one step, so “within two windows” means within one hour after the severe shift begins.

| Baseline | Arrival model | Control false episodes/day | Payment runs with any alert | Alert within 1 hour | Mean delay when detected |
|---|---|---:|---:|---:|---:|
| Fixed | Poisson | 0.0375 | 100% | 100% | 0.035 windows |
| EWMA ($\lambda=0.2$) | Poisson | 0.0700 | 100% | 100% | 0.040 windows |
| Fixed | Negative binomial | 0.0625 | 88.5% | 79.5% | 0.853 windows |
| EWMA ($\lambda=0.2$) | Negative binomial | 0.0825 | 82.0% | 73.0% | 0.848 windows |

All four configurations met the false-alert goal of at most 0.1 episodes per day. Only the Poisson configurations met the goal of detecting at least 95% of payment spikes within one hour. The EWMA did not improve these results over the fixed baseline in this experiment.

The detailed counts and per-score results are in [the evaluation data](003-count-alert-evaluation-results.json); calibration assumptions and commands are in [the experiment report](003-count-only-alerting.md).

## What This Does—and Does Not—Show

This experiment shows that count-only statistical detection can identify a strong, planted category spike while keeping the measured control alert rate below its target. It also shows that detector choice matters: the NB cases miss the one-hour target, even though they meet the false-alert budget.

It does **not** show how the detector performs on real customer operations. The evaluation uses a seeded simulator, a strong late payment shift, fixed category rates, and held-out data from the same simulator family used for calibration. Treat the rates as results for this experiment, not production guarantees. The LLM investigator is a separate stage: it receives an alert and investigates possible explanations using evidence; it does not set these statistical thresholds or decide whether counts are anomalous.

## Previewing the Math in VS Code

Open this `.md` file and choose **Open Preview to the Side** (`Ctrl+K`, then `V`) or **Open Preview** (`Ctrl+Shift+V`). The equations use standard inline (`$...$`) and display (`$$...$$`) delimiters supported by VS Code’s built-in Markdown preview with KaTeX. See the [VS Code Markdown documentation](https://code.visualstudio.com/docs/languages/markdown).
