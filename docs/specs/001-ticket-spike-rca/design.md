# Design 001: Live Ticket Spike Detection and RCA

## Components

- **Simulator:** seeded random generators produce event windows and separate ground-truth labels.
- **Detectors:** pure functions score observed counts; each method returns expected value, score name/value, threshold, and alert flag.
- **SQLite store:** append-only run, event, alert, and investigation records; evaluator-only truth is stored separately.
- **Evidence tools:** read-only, bounded queries over a single run and time window.
- **Investigator:** OpenAI Responses API tool loop, strict output schema, fixed allowlist, maximum tool-call budget.
- **API/UI:** FastAPI local service and React/TypeScript/Vite single-page operator console. SSE carries event updates; HTTP controls runs and investigations.

## Detection Methods

For Poisson counts use the upward generalized log-likelihood ratio `x log(x / mu) - (x - mu)` for `x > mu`. For negative-binomial counts, compare the fitted upward alternative mean with the baseline mean using the NB log PMF. Standardized residuals use the model variance (Poisson `mu`; NB `mu + mu^2 / r`). CUSUM accumulates positive residual excess with a configurable reference allowance. The separate Gamma-Poisson Bayes factor compares a fixed baseline-rate null with a higher-rate Gamma prior alternative; its prior is fixed in configuration and never fitted on held-out data.

## Failure Handling

- Run or stream failure leaves the run status inspectable and does not fabricate missing events.
- Invalid tool names/arguments are rejected; tools never accept arbitrary SQL or filesystem paths.
- API absence/errors yield a clear investigation failure; explicit mock mode is available for offline demonstrations and tests.
- Unresolved/contradictory evidence is represented in the report rather than hidden.

## Later Metric Extension

Add a metric registry that records metric support, denominator/exposure, aggregation interval, and model family. Use binomial/beta-binomial-style treatment for escalation proportions and a bounded/ordinal model for CSAT where appropriate. Use duration distributions or quantiles for response-time metrics. Apply standardized residuals and CUSUM to model innovations; evaluate Bayesian comparisons only with documented priors.
