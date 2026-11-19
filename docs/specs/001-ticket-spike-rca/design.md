# Design 001: Live Ticket Spike Detection and RCA

## Components

- **Simulator:** seeded random generators produce event windows and separate ground-truth labels.
- **Detectors:** pure functions score total and per-category observed counts; each method returns expected value, score name/value, threshold, and alert flag. Each emitted alert carries the selected metric/category/window plus Z-score, CUSUM, and model-specific log-likelihood ratio.
- **SQLite store:** append-only run, event, alert, and investigation records; evaluator-only truth is stored separately.
- **Evidence tools:** read-only, bounded queries over a single run and time window.
- **Investigator:** one OpenAI Agents SDK agent, structured candidate notes, read-only evidence tools, adaptive bounded turns, and persisted lifecycle/tool progress. A no-tools report-composition pass checks those notes against returned records and uses a schema that restricts citations to collected evidence handles. The investigator receives detector scores as context and does not recompute them.
- **API/UI:** FastAPI local service and React/TypeScript/Vite single-page operator console. SSE carries simulation events; HTTP controls runs and returns persisted investigation progress and reports.

## Detection Methods

For Poisson counts use the upward generalized log-likelihood ratio `x log(x / mu) - (x - mu)` for `x > mu`. For negative-binomial counts, compare the fitted upward alternative mean with the baseline mean using the NB log PMF. Standardized residuals use the model variance (Poisson `mu`; NB `mu + mu^2 / r`). CUSUM accumulates positive residual excess with a configurable reference allowance. The separate Gamma-Poisson Bayes factor compares a fixed baseline-rate null with a higher-rate Gamma prior alternative; its prior is fixed in configuration and never fitted on held-out data.

## Failure Handling

- Run or stream failure leaves the run status inspectable and does not fabricate missing events.
- Invalid tool names/arguments are rejected; tools never accept arbitrary SQL or filesystem paths.
- API absence/errors yield a clear investigation failure; explicit mock mode is available for offline demonstrations and tests. Agent traces contain tool activity and results, not private model reasoning.
- Unresolved/contradictory evidence is represented in the report rather than hidden.

## Later Metric Extension

Add a metric registry that records metric support, denominator/exposure, aggregation interval, and model family. Use binomial/beta-binomial-style treatment for escalation proportions and a bounded/ordinal model for CSAT where appropriate. Use duration distributions or quantiles for response-time metrics. Apply standardized residuals and CUSUM to model innovations; evaluate Bayesian comparisons only with documented priors.
