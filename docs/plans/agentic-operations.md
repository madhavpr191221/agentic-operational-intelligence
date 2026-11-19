# Agentic Operations Roadmap

## Product Flow

1. Generate a seeded stream of fictional product and support events.
2. Score ticket counts independently and raise an alert when evidence crosses a calibrated threshold.
3. Let the operator ask what happened.
4. Let the investigator query only read-only detector and evidence tools.
5. Return ranked explanations with evidence identifiers, uncertainty, and contradictory findings.

## Milestones

| Milestone | Deliverable | Gate |
|---|---|---|
| 0. Foundation | Clean-room charter, this roadmap, first spec/design/tasks, runnable project structure | Scope and contracts documented |
| 1. Simulator | Seeded 72-hour fictional event stream with controls and hidden truth | Repeatable runs and negative controls |
| 2. Detection | Poisson/NB likelihood scores, standardized residual, upward CUSUM, Bayesian comparison | Held-out detection evaluation reported |
| 3. Investigation | Read-only evidence tools and structured OpenAI investigator | Top-three hypothesis and evidence grounding evaluated |
| 4. Operator UI | Local run controls, live event feed, alert, investigation trace/report | End-to-end local flow usable |
| 5. Metric monitoring | CSAT, human escalation, and selected support metrics | Metric-specific models and tests |

## Operating Assumptions

- Events are streamed at an adjustable wall-clock pace but use simulated timestamps.
- SQLite is sufficient for the local demonstration; no external queue or production integration is needed.
- OpenAI calls occur only when the operator requests an investigation. Tests use a deterministic mock.
- Poisson and negative-binomial arrival assumptions are compared rather than choosing a winner in advance.
- A likelihood ratio is not called a Bayes factor. The Bayesian detector uses explicit priors and is evaluated separately.

## Evaluation Targets

On held-out seeds, target at least 80% detection of payment incidents within two 30-minute windows, no more than 0.1 false alerts per simulated day on controls, and the correct RCA cause in the top three for at least 80% of positive runs. Report evidence coverage, unsupported claims, delay, model costs, and failures alongside aggregate scores.
