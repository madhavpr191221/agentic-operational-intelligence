# Repository Guidelines

## Project Structure & Module Organization

Python backend code lives in `src/agentic_ops/`; tests are in `tests/`. The React/TypeScript/Vite UI is under `web/`. Specs, design notes, and the roadmap are under `docs/`. Runtime SQLite data belongs under `.data/` and is ignored by Git.

## Build, Test, and Development Commands

Set `UV_CACHE_DIR` to a writable user cache directory if uv's default cache path is unusable, then sync with `uv sync`; run checks with `uv run pytest`. Start the backend with `uv run uvicorn agentic_ops.api:app --reload --app-dir src`. Start the UI from `web/` with `npm install` and `npm run dev`. Once a lockfile is present, use `uv sync --locked` for reproducible installs. The backend reads only `OPENAI_API_KEY` from the ignored root `.env` when an investigation is requested; use `AGENT_MODE=mock` for an offline deterministic demo.

## Coding Style & Naming Conventions

For Python, use four spaces, type hints on public interfaces, `snake_case` functions/modules, and `PascalCase` types. Keep simulation truth separate from observable events, statistical functions pure, and LLM/provider code outside the detector core. Use TypeScript with React components in `PascalCase` and local variables in `camelCase`.

## Testing Guidelines

Place Python tests under `tests/` as `test_*.py`. Cover seeded simulation, statistical boundaries, truth isolation, tool evidence, and API behavior. Run the focused tests while editing and the full suite before handoff. Build the frontend with `npm run build` from `web/`.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout, so no repository-specific commit convention can be confirmed. Use concise imperative commit subjects (for example, `Add rate detector boundary tests`). Pull requests should explain the behavior changed, evidence or tests run, and any impact on generated data or statistical outputs. Include a linked issue or screenshots only when relevant; none are established as mandatory here.

## Data and Security

Use synthetic or public data only. Never add employer/client code, confidential schemas, credentials, or private incident records. Keep generated artifacts reproducible and identify the seed and command used when sharing results.
