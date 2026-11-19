# Agentic Operations Roadmap

## Product Flow

1. Generate a seeded stream of fictional product and support events.
2. Score total ticket arrivals and payments, orders, account, and delivery categories independently; raise calibrated category alerts.
3. Let the operator ask what happened.
4. Let one OpenAI Agents SDK investigator adaptively query read-only evidence tools; detector scores are input context, not recomputed by the model.
5. Show tool activity in the incident room, then return ranked explanations with evidence identifiers, uncertainty, and contradictory findings.

## Milestones

| Milestone | Deliverable | Gate |
|---|---|---|
| 0. Foundation | Clean-room charter, this roadmap, first spec/design/tasks, runnable project structure | Scope and contracts documented |
| 1. Simulator | Seeded 72-hour fictional event stream with controls and hidden truth | Repeatable runs and negative controls |
| 2. Detection | Poisson/NB likelihood scores, standardized residual, upward CUSUM, Bayesian comparison | Held-out detection evaluation reported |
| 3. Investigation | Read-only evidence tools and one adaptive OpenAI Agents SDK investigator | Cause support, abstention, citation validity, and tool efficiency evaluated |
| 4. Operator UI | Local run controls, category signal/alerts, live tool progress, cited report | End-to-end local flow usable |
| 5. Metric monitoring | CSAT, human escalation, and selected support metrics | Metric-specific models and tests |

## Operating Assumptions

- Events are streamed at an adjustable wall-clock pace but use simulated timestamps.
- SQLite is sufficient for the local demonstration; no external queue or production integration is needed.
- OpenAI calls occur only when the operator requests an investigation. Tests use a deterministic mock.
- The investigator uses a bounded tool loop; its UI trace reports tool activity, not private model reasoning.
- Poisson and negative-binomial arrival assumptions are compared rather than choosing a winner in advance.
- A likelihood ratio is not called a Bayes factor. The Bayesian detector uses explicit priors and is evaluated separately.

## Evaluation Targets

On held-out seeds, report per-category detection recall, false-alert episodes per simulated day, and detection delay. For RCA, report positive cause evidence support, control overclaim rate, valid citation rate, abstention, evidence coverage, tool calls, duration, and failures. See `docs/experiments/002-rca-agent-evaluation.md` for the investigator case design.
