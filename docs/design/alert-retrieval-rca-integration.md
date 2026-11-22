# Alert-to-RCA integration

**Supporting implementation detail for the [End-to-End MVP Implementation Plan](agentic_operational_intelligence_end_to_end_design_v2.md).** The master document governs scope and behavior. This note describes how the existing count-only detector connects to one evidence-bounded investigation for one category episode; it does not change detector configuration or add cross-category diagnosis.

## Goal

Connect a count-only alert to a reproducible, evidence-grounded investigation. Detection decides whether ticket volume is unusual. Retrieval finds relevant records. The investigator compares the evidence and writes a cited explanation or abstains. The investigator never changes the alert decision or executes remediation.

```text
category ticket counts
        |
        v
count-only detector ---> per-category alert episode ---> incident workspace
                                                           |
                                       automatic mode: enqueue initial investigation
                                       manual mode: wait for operator request
                                                           |
                                                           v
                                                 frozen investigation context
                                                |
                                  +-------------+-------------+
                                  |                           |
                                  v                           v
                         deterministic analysis       read-only evidence tools
                                  |                  tickets, issues, changes,
                                  |                  logs, guides, runbooks
                                  +-------------+-------------+
                                                |
                                                v
                              candidate explanations and uncertainty
                                                |
                                                v
                         citation-constrained report composition
                                                |
                                                v
                            finding, evidence, unknowns, advisory step
```

## Responsibilities

### Detection

`CountObservation` contains only a window index, UTC window bounds, and category counts. The existing detector owns the configured baseline, count scores, thresholds, and per-window alert decision. Preserve that detector configuration and alerting logic for the MVP. The episode tracker groups consecutive alert decisions within one category and ends an episode on its first quiet window. Neither detector nor episode tracker can query tickets, logs, documents, or evaluator truth. An alert is a statistical signal, not a causal diagnosis.

### Orchestration

When an episode opens, the coordinator creates its incident workspace and queues one initial investigation in automatic mode. In manual mode, the workspace waits for an authorized operator to request that same investigation. Each job is scoped to one category episode; separate categories and episodes receive separate investigations and reports. Cross-category correlation, deduplication, and joint investigation are deferred TODOs.

The initial recent-evidence scope starts with the alert window and its two preceding completed windows. It never includes a later window. Record the exact half-open UTC intervals and freeze the evidence-availability cutoff and event-sequence bound when the job is queued. Every source must enforce that cutoff, including records with late arrival. Expand recent ticket/service evidence to six hours and then at most 24 hours only when the current evidence leaves a material question unanswered; stop earlier if expansion adds no material evidence. Older persistent issues, changes, runbooks, and reference material may be retrieved separately by relevance, subject to the same availability cutoff and bounded result limits.

The investigator receives the validated alert context and evidence returned by read-only tools. It does not receive simulation truth or detector implementation details. The event store persists progress and the final report. The initial investigation searches for evidence about its one episode; missing indexes and empty results are reported as such. A first quiet window finalizes episode statistics only: it does not automatically trigger a final investigation or close the operational incident. Significant new evidence can qualify for a follow-up under the cooldown and per-episode cap in the master plan; manual follow-ups remain available.

### Retrieval and evidence

The current run store supplies incident-specific records. The local archive supplies historical tickets, issues, changes, operational documents, and runbooks. Text search combines lexical and vector candidates, reciprocal-rank fusion, and reranking. Filters for category, source, service, and availability time apply before results are returned. Product logs are numeric structured telemetry and use exact service/time filters rather than embeddings.

Evidence citations retain a stable evidence ID, source, event time, availability time when known, and document location such as section or PDF page. The report composer may cite only evidence returned during that investigation. A missing local archive is surfaced as unavailable; successful current-run searches are still usable, but the investigator must not claim it searched unavailable archive material. A normalized search activity summary records each query, source count, archive readiness, and up to five document passages for the report UI. Older reports derive this summary from their existing search trace.

### Investigator tool contract

Expose six read-only tools to the LLM. Keep lower-level Python methods internal to the evidence and analysis layer.

| Tool | Responsibility |
| --- | --- |
| `search_evidence` | Search selected customer-ticket, issue, change, or operational-document sources with a category and time scope. |
| `query_service_health` | Return structured service error counts/rates for the focus group and relevant service-log records. |
| `analyze_customer_tickets` | Compare ticket themes, reasons, and channels in the episode against an equal-length baseline. |
| `compare_incident_context` | Compare this episode's category with its own preceding equal-duration period; do not combine categories into one diagnosis. |
| `build_incident_timeline` | Order relevant tickets, service logs, issues, and changes by event time. |
| `get_runbook_guidance` | Return an advisory step and verification check grounded in previously returned evidence IDs. |

The investigator uses the evidence views relevant to this single episode, then makes only targeted follow-up calls to resolve named uncertainties. Identical calls are cached. Keep the per-investigation tool and model budgets bounded and configurable; a budget limit produces a partial/incomplete result, not a fabricated conclusion. A per-investigation call budget does not imply that multiple categories share one investigation.

## Report and safety rules

The report contains a short summary for this episode, ranked hypotheses with supporting and contradicting citations, readable search activity with retrieved document passages, unresolved questions, and non-executing recommended next steps with verification checks. Search ranking details stay collapsed by default. It must distinguish evidence for an immediate service problem from proof of its underlying technical cause. Weak or conflicting evidence yields an abstention. Citation IDs are validated against the investigation's returned evidence before persistence.

Tickets, logs, and documents are untrusted data, not instructions. Tool arguments are validated against the episode's single category, source allowlist, time bounds, result limits, and evidence IDs already returned. No tool can deploy, roll back, page, or mutate an external system.

## Evaluation

Test the boundaries separately and then together:

1. **Detection:** fixed count inputs produce the same alert regardless of ticket wording or evidence records.
2. **Retrieval:** queries find relevant records, honor category and availability cutoffs, retain citations/locations, and do not rank future records into the result.
3. **RCA:** a planted incident supports the matching explanation; healthy and unrelated/resolved controls do not receive unsupported blame; ambiguous or missing evidence leads to uncertainty or abstention.
4. **Integration:** one seeded run creates a count-only alert and category-specific episode; automatic mode queues one initial investigation (manual mode waits for an operator request); the investigator receives only cutoff-eligible episode context and returned evidence; the persisted report has valid citations and exact time scope. Verify that a future window and another category cannot enter the investigation.

Automated tests use deterministic stores, embeddings, and model doubles. They do not make paid OpenAI calls or download model weights. The existing 104-query synthetic retrieval score is a pipeline regression check, not a claim about real-world RCA accuracy. Any live model evaluation is separately invoked and reported with its model, seed, and call count.

Key design tradeoffs: clear ownership boundaries make each stage independently testable; source-specific retrieval avoids using embeddings for numeric facts; availability-time filtering prevents future evidence leakage; a bounded tool set reduces overlap and makes behavior easier to evaluate; automatic investigation on episode opening provides early findings, while manual mode remains available. Recommendations are advisory; the application does not perform remediation.
