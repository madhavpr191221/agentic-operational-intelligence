# Project Constitution

## Purpose

Build a local, interview-ready demonstration of operational anomaly detection and evidence-grounded investigation using fictional, reproducible data.

## Principles

- Generate all operational records inside this project; use no private records, code, identifiers, or credentials.
- Keep simulated incident truth inaccessible to the detector, evidence tools, and investigator.
- Treat ticket count, rates, proportions, and durations according to their statistical meaning.
- Keep statistical calculations deterministic and outside the language model.
- Cite tool evidence for factual RCA claims; distinguish association from proven causation.
- Label completed behavior separately from roadmap ideas.
- Prefer small, reviewable milestones and focused verification.

## Initial Product Boundary

The first vertical slice is a fictional payment service and customer-support workflow. It includes stochastic ticket arrivals, a count detector, read-only synthetic evidence tools, an operator-triggered investigator, and a local interface. Metric anomaly detection beyond ticket volume is a later milestone.
