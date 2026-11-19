# Spec 001: Live Ticket Spike Detection and RCA

## Problem

An operator sees an unusual ticket-volume increase and needs a fast, evidence-backed explanation across support and product systems.

## Desired Behavior

The local application streams a reproducible fictional scenario. Ticket arrivals are stochastic. Independent category detectors evaluate each 30-minute window and emit a structured alert containing its category, metric, time window, observed/baseline counts, and detector scores. The incident room shows one independently scaled chart per category and groups consecutive alert windows into category-specific episodes. Operators can select up to one episode per category and choose the exact alert window used as its representative signal. After the operator asks a question, a single tool-using investigator queries synthetic support tickets, issue records, product logs, and change events for the selected categories. The final report gives a finding for each selected category, ranks cited hypotheses, and only presents a shared cause when evidence supports it across categories.

## Initial Scenario

Runs span 72 simulated hours. The first 24 hours establish a normal baseline. A fictional payment issue opens 48 hours before the run ends and remains unresolved. Payment failures worsen near the end; customer tickets arise randomly with a probability linked to failed payments. Additional scenarios include healthy operation, an unrelated open issue, and a resolved payment issue.

## Interfaces

- Run controls: start a scenario with seed/model/speed, pause, resume, step, and inspect status.
- Event stream: timestamped generated events and detector alerts.
- Investigator: one or more operator-selected category alert episodes and a question; category findings, structured ranked hypotheses, and evidence references. The UI shows persisted tool activity while the run is in progress.
- Read-only tools: ticket search, issue search, product-log search, and change listing. Detector output is passed directly in the selected signal context rather than recalculated by the agent.

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
- The browser can run, pause, compare all four category charts with independent scales, select alert episodes and representative windows, submit a multi-category investigation, watch tool activity, and inspect category findings with cited evidence.
