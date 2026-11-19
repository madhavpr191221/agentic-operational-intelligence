# Agentic Operational Intelligence

A local, fictional operations-intelligence demo that detects category-specific support-ticket spikes and investigates them with evidence from synthetic issues, product logs, tickets, and changes. Detector scores identify unusual behavior; they do not establish causality. One OpenAI Agents SDK investigator adaptively uses read-only tools; a constrained report-composition step checks candidate explanations against returned evidence, cites only valid evidence handles, and may abstain when evidence is insufficient.

## What the demo includes

- A seeded 72-hour simulator for payment failures and healthy, unrelated-issue, and resolved-issue controls, with payments, orders, account, and delivery categories.
- Poisson and negative-binomial ticket arrivals, scored with likelihood ratios, standardized residuals, upward CUSUM, and a Gamma–Poisson Bayes factor.
- Synthetic, read-only evidence tools and an operator-triggered investigation that returns cited hypotheses.
- Empirically calibrated category-specific detector thresholds and held-out detector evaluation.
- A React interface for simulation controls, category signals/alerts, event timelines, live investigation activity, cited reports, and light/dark themes.

All records are generated locally from a reproducible seed. No external incident data is needed.

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/).
- Node.js and npm for the web interface.
- An OpenAI API key for live investigations. Mock mode is available without a key.

## Run locally (PowerShell)

Install the locked Python dependencies:

```powershell
uv sync --locked
```

In the first terminal, start the backend:

```powershell
uv run uvicorn agentic_ops.api:app --reload --app-dir src
```

For live investigations, copy `.env.example` to `.env` and set `OPENAI_API_KEY` in `.env`. The backend reads that key only when an investigation is requested. You may set `OPENAI_MODEL` in the shell to choose a model. Keep `.env` private; it is ignored by Git.

For offline mock investigations, set the mode before starting the backend:

```powershell
$env:AGENT_MODE = "mock"
uv run uvicorn agentic_ops.api:app --reload --app-dir src
```

The live workflow uses one OpenAI Agents SDK agent with a bounded adaptive tool loop, followed by one structured report-composition pass with no tools. The offline mock demonstrates the same report and activity UI without calling a model.

In a second terminal, start the web interface:

```powershell
cd web
npm ci
npm run dev
```

Open the local URL printed by Vite. Choose a scenario and start a run. When a detector alert appears, ask the investigator to examine it. The payment-failure scenario is designed to connect a ticket spike with an unresolved synthetic payment issue and supporting records.

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

Run a small live RCA evaluation with one model call per scenario (payment cause plus three controls):

```powershell
uv run python scripts/evaluate_rca.py --seed-count 1
```

This requires the OpenAI key and makes live model calls. Increase `--seed-count` for a broader estimate; the script reports evidence support, unsupported control claims, valid citations, tool calls, and duration.

## Project layout

- `src/agentic_ops/`: simulation, statistical detectors, evidence tools, API, and investigator.
- `web/`: React, TypeScript, and Vite interface.
- `tests/`: Python unit and API tests.
- `docs/specs/`: requirements, design, and task plan for the ticket-spike RCA slice.
- `scripts/`: detector calibration and evaluation.

See [the constitution](docs/CONSTITUTION.md), [roadmap](docs/plans/agentic-operations.md), and [first-slice specification](docs/specs/001-ticket-spike-rca/spec.md) for design details.
