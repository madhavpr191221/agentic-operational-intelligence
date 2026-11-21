# Agentic Operational Intelligence

A local, fictional operations-intelligence demo that detects category-specific support-ticket spikes and investigates them with evidence from synthetic issues, product logs, tickets, and changes. Detector scores identify unusual behavior; they do not establish causality. One OpenAI Agents SDK investigator adaptively uses read-only tools; a constrained report-composition step checks candidate explanations against returned evidence, cites only valid evidence handles, and may abstain when evidence is insufficient.

## What the demo includes

- A seeded 72-hour simulator for payment authorization, order-queue, and carrier-service incidents, plus healthy, unrelated-change, and resolved-issue controls.
- Poisson and negative-binomial ticket arrivals, scored with likelihood ratios, standardized residuals, upward CUSUM, and a Gamma–Poisson Bayes factor.
- Synthetic, read-only evidence tools and an operator-triggered investigation that returns cited hypotheses.
- Six grouped investigator tools search evidence, inspect service health, analyze customer themes, compare incident context, build a timeline, and retrieve advisory runbook guidance.
- One synthetic customer message per ticket, with observable reason, category, channel, and timestamp fields. Each message is the ticket record counted by the detector; no-contact customers are outside this dataset.
- Deterministic investigator tools compare categories, rank ticket reasons/channels, build an incident timeline, and compare before/after ticket and service-error rates.
- Customer-message themes are compared with an equal-length pre-incident baseline. Text searches use keyword and semantic similarity; service logs use structured fields and a strict investigation-time cutoff.
- Four fictional runbooks have editable Markdown sources and searchable text PDFs. Service and dependency guidance, historical postmortems, and deployment guidance are additional searchable Markdown documents. Citations identify the source path and page or section.
- Evidence-grounded fictional runbook suggestions include a verification check and never execute operational changes.
- Empirically calibrated category-specific detector thresholds and held-out detector evaluation.
- Four category charts, consecutive alert episodes, selectable focus windows, event timelines, category reports, and light/dark themes.
- Joint focus-window analysis in chronological order: T-2, T-1, T, and T+1 when available, with exact UTC intervals, ticket counts, service errors, and scoped citations.

All incident, ticket, service, issue, and change records are fictional and generated from a reproducible seed. No external incident data is needed.

See [Alert-to-RCA integration](docs/design/alert-retrieval-rca-integration.md) for the component boundaries, tool contract, evidence-time rules, evaluation plan, and an interview-ready architecture explanation.

### Synthetic evidence corpus and retrieval

Each support interaction is one ticket and contributes exactly one to its category count. The simulator uses separate seeded random streams for counts, event evidence, and wording, so editing customer language does not change detector inputs. The new scenarios add an order-worker backlog and a carrier API degradation; their ticket text is varied and noisy, while service logs and issue records carry separate corroborating evidence. Incident scenarios also include a nearby catalog-search change as an innocent distractor. The hidden scenario truth remains in an evaluator-only table.

The evidence world has several source types rather than one collection of PDFs: customer tickets, structured service logs, Jira-like issue histories, deployment/change records, four runbooks, service/dependency guidance, historical postmortems, and prior deployment notes. Tickets, logs, issues, and changes remain structured records; they are not duplicated as documents. Markdown is the editable source for reference documents. Runbook PDFs are retained for page-level citations; Markdown sources cite their file and section.

The archive contains 20,000 structured records (4,000 tickets, 14,000 service-log summaries, 1,000 issue records, and 1,000 change records) plus 400 generated Markdown references with 10 sections each. These are synthetic, seeded records; the full generated archive and index are stored under ignored `.data/`. A small sample and manifest are tracked under `data/` for inspection.

Text retrieval runs locally on the NVIDIA GPU. It uses `sentence-transformers/all-MiniLM-L6-v2` for embeddings, section-aware chunks of up to 240 model tokens with 36-token overlap (below the model's 256-token input limit), SQLite FTS5 and USearch HNSW for keyword and vector candidates, reciprocal-rank fusion, and `cross-encoder/ms-marco-MiniLM-L6-v2` to rerank the best candidates. Product logs stay structured and are filtered by service, category, and time rather than embedded. All time-varying sources are filtered by the investigation cutoff. The first setup downloads the two models from Hugging Face; no OpenAI embedding calls are made. OpenAI is used only when the live investigator runs.

The search evaluator uses 104 seeded, paraphrased queries with known relevant record IDs. It reports hit rate, mean reciprocal rank, and normalized discounted cumulative gain at 10, before and after reranking. This is a synthetic retrieval check, not evidence of production search quality; the seed-42 result is recorded [here](docs/results/local-rag-search-seed-42.json). See [the local hybrid RAG design](docs/design/local-hybrid-rag.md) for details and limitations.

All time-varying searches stop at the selected investigation cutoff; runbooks are static references. To regenerate the searchable PDF assets after editing their Markdown sources:

```powershell
uv run python scripts/build_runbooks.py
```

## Detection and investigation are separate

The statistical detector receives only timestamped per-category ticket counts. It emits versioned scores and alert events; it does not read ticket text, product logs, issues, or changes. The investigator runs only after an operator requests an investigation. It reads the persisted alert and uses evidence tools, but cannot import or recompute detector logic. This keeps alert quality measurable independently from the quality of the RCA explanation.

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/).
- Node.js and npm for the web interface.
- An OpenAI API key for live investigations. Mock mode is available without a key.

## Run locally (PowerShell)

Install the locked Python dependencies, including the local GPU search stack:

```powershell
uv sync --locked --extra rag-gpu
```

Verify that PyTorch can use the NVIDIA GPU and both local models run:

```powershell
uv run python scripts/check_gpu.py
```

Generate the local archive and build its search index once (and again after changing the generator or source documents):

```powershell
uv run python scripts/generate_search_corpus.py --seed 42
uv run python scripts/build_search_index.py
uv run python scripts/evaluate_search.py
```

The model files are downloaded on first use and cached by Hugging Face. The index itself is local and ignored by Git. Search across the archived data is available when the index has been built; the current simulation records remain searchable separately.

In the first PowerShell terminal, from the repository root, start the API on `127.0.0.1:8000`:

```powershell
uv run python -m uvicorn agentic_ops.api:app --app-dir src --host 127.0.0.1 --port 8000
```

For live investigations, copy `.env.example` to `.env` and set `OPENAI_API_KEY` in `.env`. OpenAI investigation is the default; clear any mock-mode setting in the current PowerShell session before starting the backend:

```powershell
Remove-Item Env:AGENT_MODE -ErrorAction SilentlyContinue
```

The backend reads the key only when an investigation is requested. You may set `OPENAI_MODEL` in the shell to choose a model. Keep `.env` private; it is ignored by Git.

For offline mock investigations, set the mode before starting the backend:

```powershell
$env:AGENT_MODE = "mock"
uv run python -m uvicorn agentic_ops.api:app --app-dir src
```

The live workflow uses one OpenAI Agents SDK agent with a bounded adaptive tool loop, followed by one structured report-composition pass with no tools. The offline mock demonstrates the same report and activity UI without calling a model.

In a second PowerShell terminal, start the web interface. Vite serves it at `http://127.0.0.1:5173/` and proxies `/api` requests to the backend on port `8000`:

```powershell
Set-Location web
npm ci
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173/`. Keep both terminals running while using the app. Choose a scenario and start a run. The **LOCAL SIMULATOR** label describes the synthetic event data; the investigator calls OpenAI when you submit an investigation.

1. Compare the category charts and select an episode in **Ticket-volume alerts**. Each episode groups consecutive 30-minute alerts. Select one episode per category.
2. Choose a focus window; the strongest signal is selected by default. The investigator compares T-2, T-1, T, and T+1 when available, using the full episode and earlier records as context. At the end of a completed run, selecting W143 includes W141, W142, and W143.
3. Click **Investigate episode**. The report lists exact UTC intervals and counts, a joint finding, supporting evidence, remaining questions, and suggested next steps. Each interval includes its start and excludes its end.
4. Open evidence records to inspect customer messages and service events. Recommendations are advisory; the app does not execute operational changes.

If a saved report predates the focus-window summary, refresh the page after the updated API is running. The API can rebuild the summary from stored run events; you do not need to rerun the simulation or investigation.

Investigation links include `?run=<run-id>&investigation=<investigation-id>#investigation-report`. Bookmark the URL after starting an investigation to reopen its saved report and charts. Opening a saved report does not call OpenAI again.

Run one API instance at a time. The command above runs without automatic reload. After backend edits, stop it with Ctrl+C and start it again. On Windows, forcibly closing a reloading server can leave its child worker running with older code; starting another server can then serve inconsistent report formats on the same port.

### Stop or restart an existing server

Press **Ctrl+C** in the terminal running the server. If that terminal is unavailable, stop the process listening on the API port:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force }
```

To stop the UI instead, use `5173` in place of `8000`. Check the port before restarting; no output means no listener was found:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

Restart with the API or UI command above. These commands stop the listener on the selected port; confirm it belongs to this app if you use that port for other projects.

If uv cannot initialize its default cache on Windows, use the ignored project-local cache before running uv:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) ".uv-cache"
```

## Tests and evaluation

From the repository root:

```powershell
uv run pytest
uv run python scripts/calibrate_detectors.py
uv run python scripts/evaluate_detectors.py
```

Build the frontend from `web/`:

```powershell
npm ci
npm run build
```

The detector evaluator reports per-category payment-signal recall, false-alert episodes per simulated day, and detection delay across held-out synthetic seeds and arrival assumptions. See [the RCA evaluation design](docs/experiments/002-rca-agent-evaluation.md) for the investigator rubric and control cases.

Run the live RCA evaluation across three incident causes and three controls. Positive scenarios are investigated only when the statistical detector alerts during the planted incident windows; controls are investigated when they emit an alert, so the report measures false-cause claims and abstention conditional on an alert:

```powershell
uv run python scripts/evaluate_rca.py --seed-count 1
```

This requires the OpenAI key and API credits. Each investigation can make multiple model calls. Increase `--seed-count` for a broader estimate; the script reports evidence support, unsupported control claims, valid citations, tool calls, and duration.

## Project layout

- `src/agentic_ops/detection/`: count-only detector contracts and statistical engine.
- `src/agentic_ops/`: simulation, runtime orchestration, evidence tools, API, and investigator.
- `web/`: React, TypeScript, and Vite interface.
- `tests/`: Python unit and API tests.
- `docs/specs/`: requirements, design, and task plan for the ticket-spike RCA slice.
- `scripts/`: detector calibration and evaluation.

See [the constitution](docs/CONSTITUTION.md), [roadmap](docs/plans/agentic-operations.md), and [first-slice specification](docs/specs/001-ticket-spike-rca/spec.md) for design details.
