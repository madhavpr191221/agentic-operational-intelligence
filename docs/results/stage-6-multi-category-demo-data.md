# Stage 6: Multi-category seeded demo data

## What changed

The live monitor now offers the existing Payments Normal, Medium spike, and High spike presets plus selectable high-spike scenarios for Orders, Account sign-in, and Delivery. Each run is seeded and plants one category-specific incident across four 30-minute windows beginning at replay window 216. Other category counts remain visible as monitoring context.

The Orders and Delivery service telemetry now follows the configured incident window in both live and finite simulations. The Account scenario adds sign-in and verification-code complaints, account-api/identity-service telemetry, and an issue/change record. Tickets, service logs, and operational records are generated independently; their alignment supports investigation but does not prove causation.

An Account runbook, historical identity-service review, deployment note, and synthetic retrieval topics were added. Runbook PDFs are generated from Markdown. Historical corpus output remains reproducible and local under `.data/corpus`.

No detector scores, thresholds, or SQLite schema were changed. Each selected scenario creates its own category episode and investigation; cross-category correlation is deferred.

## Verification

- Full backend suite passes: `uv run pytest -p no:cacheprovider -q` (105 passed, 9 subtests).
- Each fixed non-Payments seed alerts in its own category during the planted four-window interval; category-specific service telemetry is severe in those same windows.
- The seven-day UI builds successfully with `npm run build`.
- The 20,000-record local corpus was regenerated with `uv run python scripts/generate_search_corpus.py` (seed 42); its manifest now records 112 search queries.
- Runbook PDFs were regenerated from Markdown; the Account PDF is indexed with page citations.

The test run emitted a non-fatal pytest cache warning on this Windows workspace. No paid OpenAI calls were made.
