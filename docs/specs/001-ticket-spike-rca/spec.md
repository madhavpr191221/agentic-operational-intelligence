# Spec 001: Live Ticket Spike Detection and RCA

## Problem

An operator sees an unusual ticket-volume increase and needs a fast, evidence-backed explanation across support and product systems.

## Desired Behavior

The local application streams a reproducible fictional scenario. Ticket arrivals are stochastic. A detector evaluates each 30-minute window and emits a structured alert. After the operator asks a question, an investigator may call the detector and query synthetic support tickets, issue records, product logs, and change events. The final report ranks hypotheses and cites evidence IDs.

## Initial Scenario

Runs span 72 simulated hours. The first 24 hours establish a normal baseline. A fictional payment issue opens 48 hours before the run ends and remains unresolved. Payment failures worsen near the end; customer tickets arise randomly with a probability linked to failed payments. Additional scenarios include healthy operation, an unrelated open issue, and a resolved payment issue.

## Interfaces

- Run controls: start a scenario with seed/model/speed, pause, resume, step, and inspect status.
- Event stream: timestamped generated events and detector alerts.
- Investigator: selected alert plus operator question; structured ranked hypotheses and evidence references.
- Read-only tools: detector query, ticket search, issue search, log query, and change listing.

## Statistical Behavior

- Compare Poisson and negative-binomial count models.
- Provide one-sided Poisson/NB log-likelihood ratios for abrupt increases.
- Provide model-based standardized residuals for interpretation and comparison.
- Provide upward CUSUM over residuals for persistent increases.
- Provide a separate Gamma-Poisson Bayes factor with documented prior assumptions.
- Calibrate thresholds on designated calibration seeds; evaluate on separate held-out seeds.

## Constraints and Non-Goals

- No external ticketing or production-system integrations.
- The simulator's planted-cause labels are evaluator-only.
- The agent does not calculate statistics or write/change synthetic records.
- CSAT, escalation, and other metric anomaly detection are out of the first milestone.

## Acceptance Criteria

- Same seed and run controls reproduce the same events; other seeds vary.
- The planted cause is never present in tool-visible event payloads.
- Detector metrics and RCA metrics are computed from held-out seeds.
- Every factual report claim references returned evidence; weak evidence permits abstention.
- The browser can run, pause, observe an alert, submit an investigation, and inspect its report.
