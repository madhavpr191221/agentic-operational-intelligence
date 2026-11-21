# Realistic Synthetic Evidence Corpus

## Purpose

Give the investigator a nontrivial, fictional incident world to search. Ticket counts remain the detector's only input. Customer wording, service telemetry, issue records, changes, and runbooks are separate evidence available after an alert.

## Observable event sources

- **Customer tickets:** one record per support interaction, with category, timestamp, reason, channel, title, and varied message wording. The number of ticket records must equal the simulated category count exactly.
- **Service logs:** structured service name, request and error counts, and incident-specific fields such as queue depth, oldest job age, carrier latency, or rate limits.
- **Issue tracker:** fictional issue creation, update, and resolution events with issue key, status, owner context, and timestamps.
- **Change log:** plausible service changes, including unrelated changes near incidents as distractors.
- **Reference documents:** service/dependency guides, historical postmortems, deployment guidance, and prior deployment notes are editable Markdown and cite their source path and section.
- **Runbooks:** editable Markdown is the source; searchable text PDFs preserve page-level citations.

## Scenario set

| World | Count signal | Independent supporting evidence |
|---|---|---|
| Payment authorization failure | Payments ticket arrivals rise | Authorization errors, payment issue, and varied customer symptoms |
| Order processing backlog | Orders ticket arrivals rise | Queue depth and age rise; order status complaints |
| Carrier API degradation | Delivery ticket arrivals rise | Carrier errors, latency and rate limits; tracking complaints |
| Healthy control | No planted count shift | Ordinary message and telemetry variation |
| Unrelated change control | No matching payment count shift | Search service issue/change near the end of the run |
| Resolved issue control | No late planted count shift | Payment issue is resolved before the final windows |

Count randomness, evidence randomness, and customer wording use independent seeded streams. Text changes cannot alter counts or detector output. Hidden scenario truth is stored only for evaluation and is not exposed to evidence tools.

## Retrieval and time boundary

Text searches over customer tickets, issue descriptions, change summaries, and operational reference documents combine SQLite FTS5 lexical candidates with local GPU embeddings from `sentence-transformers/all-MiniLM-L6-v2`. Reciprocal-rank fusion combines keyword and vector ranks; `cross-encoder/ms-marco-MiniLM-L6-v2` reranks the strongest candidates. SQLite stores searchable metadata and payloads, and USearch stores the cosine HNSW vectors. Service telemetry remains structured and is queried by service and time, not embedded. Runbooks are extracted per PDF page; Markdown references use heading-aware sections and bounded overlapping chunks. Evidence retains source path, page or section, availability time, and category. Dated content is filtered at the investigation cutoff.

The generated archive contains 20,000 records across tickets, service logs, issue records, and changes, plus 400 Markdown references with 10 sections each. Its generated files and index are ignored under `.data/`; the source generator, small sample, and manifest are tracked. See [the local hybrid RAG design](../design/local-hybrid-rag.md) for index setup and retrieval evaluation.

Time-varying evidence searches include records only up to the selected investigation cutoff. Static runbook pages are not time-filtered. Ticket-theme comparisons report reason counts and shares for the selected episode and an equal-length preceding interval, with customer examples. The agent may make targeted follow-up searches; it must consider competing explanations and state what the records cannot establish.

## Safety and scope

- All customer interactions, services, issues, and changes are fictional.
- The detector package continues to accept only timestamped category counts.
- The investigator is read-only; runbook guidance is advisory and never executes an action.
- Unit tests use deterministic embedding and reranking fakes; they do not download models or make paid API calls.

## Acceptance checks

- Repeated seeds reproduce the same count stream, even when ticket text is materialized.
- Per-window support-ticket records reconcile exactly to category counts.
- Each planted incident changes the intended category while its structured telemetry and customer themes provide distinct evidence.
- Control worlds do not receive a planted target-category cause.
- Searches cannot return records after their alert cutoff.
- PDF text can be extracted with page numbers and citations resolve to the source runbook page; Markdown passages retain section-level provenance.
- Hybrid retrieval finds a paraphrased record and reuses cached vectors on repeat searches.
- Local index filters enforce source, category, service, and availability cutoffs.
- The seeded 104-query evaluator reports retrieval metrics before and after reranking; these synthetic scores are regression checks, not production-quality estimates.
