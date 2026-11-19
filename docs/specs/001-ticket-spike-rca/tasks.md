# Tasks 001: Live Ticket Spike Detection and RCA

## Stage A - Foundation

- [x] Create clean-room charter and roadmap.
- [x] Define the first scenario and interface contract.
- [x] Set up package, settings, and SQLite schema.

## Stage B - Synthetic Data and Detection

- [x] Implement seeded Poisson and negative-binomial event generation.
- [x] Add positive, healthy, resolved, and unrelated-issue scenarios.
- [x] Implement LLR, standardized residual, upward CUSUM, and Gamma-Poisson BF.
- [x] Add calibration and held-out evaluation report.

## Stage C - Evidence and RCA

- [x] Implement read-only query tools with evidence IDs.
- [x] Add structured OpenAI Responses tool loop and mock investigator.
- [x] Verify truth isolation and report grounding.

## Stage D - Local Operator Experience

- [x] Add run/event/investigation API and SSE stream.
- [x] Build UI for scenario controls, timeline, alert, and RCA evidence.
- [x] Document local startup and optional OpenAI configuration.

## Stage E - Later Metrics

- [ ] Add CSAT and escalation metric definitions (deferred; ticket counts are the current vertical slice).
- [ ] Select and test metric-specific models before adding further metrics.
