# Repository Guidelines

## Project Structure & Module Organization

Python backend code lives in `src/agentic_ops/`; tests are in `tests/`. The React/TypeScript/Vite UI is under `web/`. Specs, design notes, and the roadmap are under `docs/`. Runtime SQLite data belongs under `.data/` and is ignored by Git.

## Build, Test, and Development Commands

From the repository root, sync dependencies with `uv sync --locked` and start the API with `uv run python -m uvicorn agentic_ops.api:app --app-dir src --host 127.0.0.1 --port 8000`. In a second terminal, run `Set-Location web`, `npm ci` (first install), then `npm run dev -- --host 127.0.0.1`; open `http://127.0.0.1:5173/`. Keep both servers running; Vite proxies API requests to port 8000. Set `UV_CACHE_DIR` to a writable user cache directory if uv's default cache path is unusable. The backend reads only `OPENAI_API_KEY` from the ignored root `.env` when an investigation is requested; use `AGENT_MODE=mock` only for an offline deterministic demo.

Run one API instance without automatic reload. Stop it with Ctrl+C before restarting after backend edits. If its terminal is unavailable, stop the API listener with:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force }
```

Use port `5173` for the UI. Verify the selected port is free before restarting; avoid terminating unrelated project processes. Saved investigation URLs reopen reports without another OpenAI call.

## Coding Style & Naming Conventions

For Python, use four spaces, type hints on public interfaces, `snake_case` functions/modules, and `PascalCase` types. Keep simulation truth separate from observable events, statistical functions pure, and LLM/provider code outside the detector core. Use TypeScript with React components in `PascalCase` and local variables in `camelCase`.

## Testing Guidelines

Place Python tests under `tests/` as `test_*.py`. Cover seeded simulation, statistical boundaries, truth isolation, tool evidence, and API behavior. Run the focused tests while editing and the full suite before handoff. Build the frontend with `npm run build` from `web/`.

## Commit & Pull Request Guidelines

History uses concise imperative subjects, sometimes with a `feat:` prefix. Follow that style (for example, `Add rate detector boundary tests`). Pull requests should explain the behavior changed, checks run, and impacts on generated data or detector outputs. Include linked issues or screenshots when relevant.

## Data and Security

Use synthetic or public data only. Never add employer/client code, confidential schemas, credentials, or private incident records. Keep generated artifacts reproducible and identify the seed and command used when sharing results.
