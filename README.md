# Agentic Operational Intelligence

A local, fictional operations-intelligence demo that detects category-specific support-ticket spikes and investigates them with evidence from synthetic issues, product logs, tickets, and changes. Detector scores identify unusual behavior; they do not establish causality. One OpenAI Agents SDK investigator adaptively uses read-only tools; a constrained report-composition step checks candidate explanations against returned evidence, cites only valid evidence handles, and may abstain when evidence is insufficient.

## What the demo includes

- A seeded 72-hour simulator for payment failures and healthy, unrelated-issue, and resolved-issue controls, with payments, orders, account, and delivery categories.
- Poisson and negative-binomial ticket arrivals, scored with likelihood ratios, standardized residuals, upward CUSUM, and a Gamma–Poisson Bayes factor.
- Synthetic, read-only evidence tools and an operator-triggered investigation that returns cited hypotheses.
- One synthetic customer message per ticket, with observable reason, category, channel, and timestamp fields. Each message is the ticket record counted by the detector; no-contact customers are outside this dataset.
- Deterministic investigator tools compare categories, rank ticket reasons/channels, build an incident timeline, and compare before/after ticket and service-error rates.
- Evidence-grounded fictional runbook suggestions include a verification check and never execute operational changes.
- Empirically calibrated category-specific detector thresholds and held-out detector evaluation.
- Four category charts, consecutive alert episodes, selectable focus windows, event timelines, category reports, and light/dark themes.
- Joint focus-window analysis in chronological order: T-2, T-1, T, and T+1 when available, with exact UTC intervals, ticket counts, service errors, and scoped citations.

All records are generated locally from a reproducible seed. No external incident data is needed.

## Detection and investigation are separate

The statistical detector receives only timestamped per-category ticket counts. It emits versioned scores and alert events; it does not read ticket text, product logs, issues, or changes. The investigator runs only after an operator requests an investigation. It reads the persisted alert and uses evidence tools, but cannot import or recompute detector logic. This keeps alert quality measurable independently from the quality of the RCA explanation.

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/).
- Node.js and npm for the web interface.
- An OpenAI API key for live investigations. Mock mode is available without a key.

## Run locally (PowerShell)

Install the locked Python dependencies:

```powershell
uv sync --locked
```

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

Run a small live RCA evaluation with one investigation per scenario (payment cause plus three controls):

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
