# Agentic Operational Intelligence

A local, fictional operations-intelligence demo that replays ticket activity, detects category-specific spikes, and investigates emitted alert episodes using synthetic issues, product logs, tickets, and changes. Detector scores identify unusual behavior; they do not establish causality. The OpenAI Agents SDK investigator adaptively uses read-only tools; a constrained report-composition step checks candidate explanations against returned evidence, cites only valid evidence handles, and may abstain when evidence is insufficient.

## What the demo includes

- Seeded synthetic ticket and service activity for payment authorization, order-queue, and carrier-service incidents, plus healthy and distractor controls. The live monitor uses its own ten-seed replay pool.
- Poisson and negative-binomial ticket arrivals, scored with likelihood ratios, standardized residuals, upward CUSUM, and a Gamma–Poisson Bayes factor.
- Synthetic, read-only evidence tools and an automatic investigation for each new alert episode, with a manual follow-up action in the incident inbox.
- Six grouped investigator tools search evidence, inspect service health, analyze customer themes, compare incident context, build a timeline, and retrieve advisory runbook guidance.
- Each selected category gets a focused reference-library search; the report shows source counts and matching guide passages, with retrieval ranking details expandable.
- One synthetic customer message per ticket, with observable reason, category, channel, and timestamp fields. Each message is the ticket record counted by the detector; no-contact customers are outside this dataset.
- Deterministic investigator tools compare categories, rank ticket reasons/channels, build an incident timeline, and compare before/after ticket and service-error rates.
- Customer-message themes are compared with an equal-length pre-incident baseline. Text searches use keyword and semantic similarity; service logs use structured fields and a strict investigation-time cutoff.
- Four fictional runbooks have editable Markdown sources and searchable text PDFs. Service and dependency guidance, historical postmortems, and deployment guidance are additional searchable Markdown documents. Citations identify the source path and page or section.
- Evidence-grounded fictional runbook suggestions include a verification check and never execute operational changes.
- Empirically calibrated category-specific detector thresholds and held-out detector evaluation.
- A live synthetic monitor with Normal, Medium spike, and High spike replay presets. It releases seeded ticket events over time, updates all category counts within each 30-minute window, and shows a small rolling sample of arriving tickets.
- Automatic investigation by default, with a manual mode that records alerts and waits for an operator to start each investigation.
- A seven-day seeded replay: four days of fixed history warm the existing detector, then three days stream over about two minutes. The stream can be paused, resumed, stepped by one 30-minute window, or replayed with the same seed. It ends explicitly and does not loop; it is synthetic replay, not a live external ticket connection.

All incident, ticket, service, issue, and change records are fictional and generated from a reproducible seed. No external incident data is needed.

See [Alert-to-RCA integration](docs/design/alert-retrieval-rca-integration.md) for the component boundaries, tool contract, evidence-time rules, evaluation plan, and an interview-ready architecture explanation.

The [continuous detection and incident investigation architecture](docs/design/event-driven-incident-system.md) is implemented as a local prototype: count alerts are saved to a SQLite outbox, opened episodes create investigation jobs, and the monitor shows reports and retrieved passages. It uses one API process and one background worker. The live monitor releases only events whose simulated time has arrived; real connectors, replay recovery after an API restart, automatic evidence-triggered follow-ups, and production-grade job recovery remain future work.

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

The statistical detector receives only timestamped per-category ticket counts. It emits scores and alert events; it does not read ticket text, product logs, issues, or changes. In automatic mode, an opened episode creates a persisted investigation job. In manual mode, the alert waits for an operator to start that job. The investigator reads the persisted alert and uses evidence tools, but cannot import or recompute detector logic. This keeps alert quality measurable independently from the quality of the RCA explanation.

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

If you install or repair the GPU search dependencies while the API is already running, restart the API. A saved report keeps the search result from when it was created; start a new investigation to search the archive again.

For live investigations, copy `.env.example` to `.env` and set `OPENAI_API_KEY` in `.env`. OpenAI investigation is the default; the launcher clears any inherited mock-mode setting unless you pass `-Mock`.

The backend reads the key when an investigation job runs. The demo checks that an OpenAI key is configured before it lets you start, and runs investigations automatically when alerts appear. Starting a demo that emits alerts can use OpenAI API credits. You may set `OPENAI_MODEL` in the shell to choose a model. Keep `.env` private; it is ignored by Git.

The live workflow uses one OpenAI Agents SDK agent with a bounded adaptive tool loop, followed by one structured report-composition pass with no tools. The offline mock demonstrates the same report and activity UI without calling a model.

### Start the app with one command

```powershell
.\scripts\dev.ps1
```

Or double-click `scripts/start-dev.cmd` in File Explorer; it opens the PowerShell launcher for you.

This starts the API and web interface in the background, checks that both are ready, then returns to the PowerShell prompt. Open `http://127.0.0.1:5173/` in your browser. Add `-OpenBrowser` if you want the script to open it automatically. If Python or web dependencies are missing, the script installs them from the lockfiles. Logs are saved under `.data/dev/`.

Stop both services with:

```powershell
.\scripts\dev.ps1 -Stop
```

Or double-click `scripts/stop-dev.cmd`.

The stop command stops only recognized services on ports `8000` and `5173`; it leaves other processes alone. `-Mock` starts an offline demo, but the severity-based demo flow requires OpenAI and will be disabled in mock mode. Keep `.env` private; it is ignored by Git.

If PowerShell blocks running local scripts, launch it with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

Use the same command with `-Stop` to stop both services when the default policy blocks local scripts.

1. Opening the main UI no longer starts a replay. Expand **Replay settings**, choose **Normal**, **Medium spike**, or **High spike**, then press **Start demo**. The selected seed is shown; use **Replay this seed** to repeat it. A saved URL with `?run=...` still opens that specific replay.
2. Days 1-4 load as fixed history to establish the detector baseline. Days 5-7 stream in about two minutes. Watch all four category counts update as tickets arrive; only completed 30-minute windows are scored, using the existing count-only detector. A scrolling sample shows recent ticket wording, while the counts include every ticket.
3. The Medium and High presets plant a payment spike at the same point in the replay, while Normal has no planted incident and can still produce a natural false alert. When a category alerts, its episode appears under **Alerts and investigations** and the investigation starts automatically. The card shows whether it is queued, running, complete, or needs attention.
4. Read the report summary and the visible **Recommended next step**. Recommendations are advisory; the app does not execute operational changes. Expand **Evidence and retrieved guides** and **What remains uncertain** to inspect sources and limits.
5. Pause, resume, step, or stop a replay using its controls. Starting a new replay keeps earlier runs available in replay history. OpenAI investigations can use API credits.

Pause freezes simulated time; **Step window** releases the rest of the current window, scores it, then pauses again. **Replay complete** means the finite synthetic source ended; it does not mean the service recovered. The completed replay remains visible until you reload or start another one. This demo does not monitor a live ticket source. Replay workers do not resume after an API process restart.

Run one API instance at a time. After backend edits, stop and restart it with `.\scripts\dev.ps1 -Stop` and `.\scripts\dev.ps1`.

### Stop or restart an existing server

Use the launcher to stop both services:

```powershell
.\scripts\dev.ps1 -Stop
```

The launcher checks port ownership and leaves unrecognized processes running. If you started either service manually, stop it with Ctrl+C in its terminal.


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
