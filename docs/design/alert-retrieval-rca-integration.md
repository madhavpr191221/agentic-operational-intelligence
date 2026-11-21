# Alert-to-RCA integration

## Goal

Connect a count-only alert to a reproducible, evidence-grounded investigation. Detection decides whether ticket volume is unusual. Retrieval finds relevant records. The investigator compares the evidence and writes a cited explanation or abstains. The investigator never changes the alert decision or executes remediation.

```text
category ticket counts
        |
        v
count-only detector ---> alert episode ---> operator selects episode
                                                |
                                                v
                                      investigation context
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

`CountObservation` contains only a window index, UTC window bounds, and category counts. The detector owns the baseline, count scores, thresholds, and alert-episode grouping. It cannot import or query tickets, logs, documents, or evaluator truth. Its output is an alert signal, not a causal diagnosis.

### Orchestration

The application service validates the selected run and episode, builds the deterministic episode summary, and starts the investigation only after operator action. The summary carries the whole episode and a local focus group of up to `T-2`, `T-1`, `T`, and `T+1`, in chronological order when those windows exist. It records the exact half-open UTC intervals and identifies the selected focus window. Records after the declared investigation cutoff are excluded.

The LLM receives the validated signal and evidence returned by tools. It does not receive simulation truth or detector implementation state. The event store persists progress and the final report. Each selected category receives at least one focused operational-document search using an observed symptom or service name; missing indexes and empty results are reported as such.

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
| `compare_incident_context` | Compare categories over the episode and compare the alerted category with its preceding equal-duration period. |
| `build_incident_timeline` | Order relevant tickets, service logs, issues, and changes by event time. |
| `get_runbook_guidance` | Return an advisory step and verification check grounded in previously returned evidence IDs. |

The agent must inspect all six evidence views for each selected category, then make only targeted follow-up calls to resolve named uncertainties. Identical calls are cached. A total ceiling of 32 tool calls permits the six required calls for each of four categories plus a small follow-up budget; each individual tool is capped at eight calls. Reaching a ceiling produces a partial/incomplete result, not a fabricated conclusion.

## Report and safety rules

The report contains a short overall summary, a finding per selected category, ranked hypotheses with supporting and contradicting citations, readable search activity with retrieved document passages, unresolved questions, and non-executing recommended next steps with verification checks. Search ranking details stay collapsed by default. It must distinguish evidence for an immediate service problem from proof of its underlying technical cause. Weak or conflicting evidence yields an abstention. Citation IDs are validated against the investigation's returned evidence before persistence.

Tickets, logs, and documents are untrusted data, not instructions. Tool arguments are validated against the selected categories, source allowlist, time bounds, result limits, and evidence IDs already returned. No tool can deploy, roll back, page, or mutate an external system.

## Evaluation

Test the boundaries separately and then together:

1. **Detection:** fixed count inputs produce the same alert regardless of ticket wording or evidence records.
2. **Retrieval:** queries find relevant records, honor category and availability cutoffs, retain citations/locations, and do not rank future records into the result.
3. **RCA:** a planted incident supports the matching explanation; healthy and unrelated/resolved controls do not receive unsupported blame; ambiguous or missing evidence leads to uncertainty or abstention.
4. **Integration:** one seeded run creates a count-only alert, an operator selects its episode, the investigator gets only that context and returned evidence, and the persisted report has valid citations and exact time scope.

Automated tests use deterministic stores, embeddings, and model doubles. They do not make paid OpenAI calls or download model weights. The existing 104-query synthetic retrieval score is a pipeline regression check, not a claim about real-world RCA accuracy. Any live model evaluation is separately invoked and reported with its model, seed, and call count.

## Interview explanation

> "The detector is a separate statistical component that sees only category counts and emits an alert episode. An operator starts an investigation. The application gives the agent a validated, time-bounded context and read-only tools. Text records use hybrid retrieval; numeric service telemetry uses structured queries; deterministic functions perform comparisons and timelines. The model weighs competing explanations and drafts a citation-constrained report, including uncertainty. We test detection, retrieval, and RCA independently, then test the complete path on an incident and controls. Remediation remains advisory."

Key design tradeoffs to explain: simple ownership boundaries make each stage independently testable; source-specific retrieval avoids using embeddings for numeric facts; availability-time filtering prevents future evidence leakage; a small tool set reduces overlap and makes behavior easier to evaluate; human-triggered investigations and non-executing recommendations keep the first version reviewable.
