# Stage 5: End-to-end demonstration and resilience

## What changed

- A seeded live replay now persists its event cursor, current-window ticket counts, simulated timestamp, and completed-window count in SQLite. Each released event and its next cursor are committed atomically under a stable event ID.
- After an API process restart, an interrupted seeded replay is restored as the same run at its saved cursor and remains paused until an operator chooses **Resume**. This preserves the distinction between simulated replay time and application time.
- Non-resumable legacy runs are marked stopped with a visible restart message. Existing completed runs and reports remain available.
- The Live monitor no longer stops active runs simply because the page is opened. It restores the most recent active or restart-recovered replay and shows a banner with its saved simulated time. The banner explains that durable investigations and notification delivery can continue while the replay is paused.
- Durable investigation and notification workers continue their existing independent lease/outbox recovery. A recovered investigation may call the configured investigator; the monitor banner makes this explicit.
- Detector observations, scores, and alert events use stable IDs. Reprocessing a window after a crash between scoring and cursor advancement does not duplicate committed detector events or incidents.
- README and repository instructions now describe replay recovery and the possibility of background investigation work while replay is paused.

## Try the complete local workflow

1. Start the app with `./scripts/dev.ps1 -OpenBrowser` and choose a High spike replay. This is synthetic data; automatic investigations use OpenAI and may incur API charges.
2. Watch **Live monitor** as tickets arrive, counts change, and completed 30-minute windows are scored. An alert opens an incident while its investigation progresses.
3. Open **Incident inbox** to inspect the report, evidence cutoff, attention assessment, and simulated email draft/history. A simulated email is stored and displayed locally; it is never sent over the network.
4. Acknowledge the notification, record an externally reported action, review subsequent monitoring, then resolve the incident with a reason. Resolution is a human decision; replay completion and quiet windows do not resolve it.
5. To see replay recovery, restart the API during a live replay. Reopen **Live monitor**: it should show the same run and simulated timestamp with **Replay recovered after app restart - paused**. Press **Resume** to continue. Investigations and notification jobs may have continued during the pause.

## Checks

- `tests/test_service.py`: **8 passed**, including recovery from a cursor after one released ticket, no duplicate ticket on resume, and idempotent processing of a completed window.
- Full backend suite: **102 passed, 8 subtests passed**.
- `npm run build` from `web/`: passed.
- `uv run python -m compileall -q src tests`: passed.
- `git diff --check`: passed (Git printed only LF/CRLF working-copy notices).
- Automated checks use `MockInvestigator` and simulated delivery; no paid model calls or network email were used.

## Limits

- This is a local synthetic demonstration, not a connector to a live ticket system.
- On process restart, the replay pauses at the last durable released-event cursor. A crash after the final event of a window but before its observation/checkpoint is saved can cause deterministic window processing to run again; stable event identities and detector checkpoints prevent duplicate committed results.
- Durable investigator and notification workers are independent of replay pause and can continue after restart. Automatic resumed investigation can use OpenAI when configured.
- The local operator identity and delivery configuration are demonstration settings, not production authentication or a production email integration.
