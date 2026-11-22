# Local hybrid retrieval for incident investigation

**Supporting retrieval implementation note for the [End-to-End MVP Implementation Plan](agentic_operational_intelligence_end_to_end_design_v2.md).** The master document governs when investigations run, their category/episode scope, and evidence lookback. This document describes the local synthetic archive and retrieval pipeline; it does not redefine the investigation workflow.

## Goal

Give the investigator a larger, varied fictional evidence world and a real retrieval pipeline, while keeping the ticket-count detector independent. The investigator can search current-run evidence plus a dated archive of tickets, issue records, changes, operational references, and runbooks. The statistical detector still receives only category counts.

## Seeded corpus

Run `uv run python scripts/generate_search_corpus.py --seed 42` to create the ignored archive under `.data/corpus/`:

| Source | Records | Search treatment |
| --- | ---: | --- |
| Customer tickets | 4,000 | Text chunks, vector and keyword search |
| Service log summaries | 14,000 | Structured filtering; never embedded |
| Issue tracker records | 1,000 | Text chunks, vector and keyword search |
| Change records | 1,000 | Text chunks, vector and keyword search |
| Generated Markdown references | 400 documents, 10 sections each | One section per passage, split only if oversized |
| Existing runbooks and knowledge documents | Repository source files | PDF page or Markdown section passages |

The four categories have distinct service and symptom themes. Tickets include multiple channels, varied wording, unrelated customer reasons, and incomplete symptoms. The archive also contains nearby but irrelevant changes and older incident context. All records are fictional. Generator seed, source counts, date range, and schema are written to `.data/corpus/manifest.json`; `data/corpus_manifest.json` and a 10-record sample are tracked for inspection.

The archive's event times cover October through December 2025. Each record has `simulated_at` and `available_at`; retrieval uses `available_at` for the as-of cutoff. This allows the investigator to find older precedent without treating that precedent as proof of the current cause.

## Build pipeline

Install the optional local-GPU dependencies with `uv sync --locked --extra rag-gpu`. The lock selects the PyTorch CUDA 13.0 wheel index for this extra. Verify actual model execution with `uv run python scripts/check_gpu.py`.

`uv run python scripts/build_search_index.py` builds `.data/search_index/`:

1. Structured JSON records and document passages are assigned stable evidence IDs and metadata: source, category, event time, availability time, file path, section/page, and chunk number.
2. Markdown follows heading hierarchy. Each section becomes its own passage, with its heading included as context; a long section is split into 240 model-token chunks with 36-token overlap. This stays below the embedding model's 256-token input limit. PDF text keeps page citations and is split only if a page is oversized.
3. SQLite stores payloads and metadata. SQLite FTS5 provides lexical candidates. Service logs are available through structured filters, not text embeddings.
4. `sentence-transformers/all-MiniLM-L6-v2` embeds searchable text on `cuda:0`. A USearch cosine HNSW index stores the vectors.
5. At query time, keyword and approximate-vector ranks are combined with reciprocal-rank fusion. `cross-encoder/ms-marco-MiniLM-L6-v2` reranks the strongest candidates on the GPU.

The index is local, reproducible, and ignored by Git. Model weights are downloaded once from Hugging Face and cached by that library. The application uses OpenAI only for the live investigation and report generation; embeddings and reranking are local.

## Time and source constraints

Every investigation is scoped to one category episode and has a frozen evidence-availability cutoff. The first recent-incident search covers the alert window and two preceding completed windows. Expand recent ticket and service evidence to six hours and then at most 24 hours only when the current evidence leaves a material question unanswered; stop earlier when expansion adds no material evidence. Older persistent issues, changes, runbooks, and references are a separate relevance search and may extend beyond that recent-evidence horizon.

Every dated corpus query must enforce `available_at <= investigation cutoff`; use a lower time bound where it applies to the selected recent-evidence expansion. Category and service filters are applied before results are returned. The current simulation evidence stays in its run-scoped SQLite store; the archive index supplies historical context. No future window, post-cutoff late arrival, or another category's episode may leak into this investigation. Evidence includes source and event/availability timestamps so a historical match cannot be presented as an event from the live episode or as proof of its cause.

The product logs are numeric and structured. Search filters them by category, service, and time and returns request/error counts and rates; it does not ask a text embedding to reason about numeric telemetry. The LLM sees returned records only through the evidence tools and cannot access generator truth labels.

## Retrieval evaluation

`uv run python scripts/evaluate_search.py` evaluates 104 deterministic, paraphrased topic queries with known relevant evidence IDs. It reports hit rate at 10, mean reciprocal rank, and normalized discounted cumulative gain at 10 for the fused candidate ranking and the cross-encoder reranked ranking. Use `--output .data/corpus/search-evaluation.json` to save a local result.

This checks whether retrieval can find planted synthetic matches and whether reranking improves their order. The generator labels relevant records by latent synthetic topic, which makes the benchmark useful for regression testing but easier and cleaner than human-labeled workplace search. The score is not an estimate of real-world RCA accuracy. Future work should add ambiguous queries, hard negatives, query labels reviewed independently of the generator, and evidence-level precision/recall by source.

The initial seed-42 run on the RTX 5060 Laptop GPU achieved 99.0% hit rate at 10 and 0.969 mean reciprocal rank before reranking; reranking retained the same hit rate and raised mean reciprocal rank to 0.974. Normalized discounted cumulative gain at 10 rose from 0.964 to 0.972. The exact run metadata and scores are saved in [`docs/results/local-rag-search-seed-42.json`](../results/local-rag-search-seed-42.json).

## Rebuild and inspect

```powershell
uv run python scripts/generate_search_corpus.py --seed 42
uv run python scripts/check_gpu.py
uv run python scripts/build_search_index.py
uv run python scripts/evaluate_search.py --output .data/corpus/search-evaluation.json
```

Changing source documents requires rebuilding the index. Changing the generator or seed requires regenerating the archive and then rebuilding. The index is not committed because it is large and can be reconstructed deterministically.
