# Stage 1: Episode events and incident contracts

## What changed

- The existing count-only detector configuration, scores, thresholds, and episode tracker remain unchanged. Consecutive alert windows update one category episode; the first quiet window ends it.
- Automatic mode creates one initial investigation when an episode opens. Manual mode waits for an operator request. A quiet window finalizes episode statistics without scheduling a final investigation or closing the operational incident.
- The existing incident `status` remains the statistical episode state (`active` or `ended`). The new `operational_status` is `open`. Old incidents receive `open` through an additive, repeatable SQLite migration: a statistical episode ending does not establish operational resolution. Existing jobs and reports are retained.
- Applied episode-event IDs are persisted transactionally with their effects. Re-delivering an older event cannot duplicate an investigation or revert an ended episode. Previously delivered outbox events are registered during migration.
- The live monitor displays the alert episode state and operational incident state separately. When an episode ends, it explains that this does not confirm the customer issue is resolved.

## See it in the UI

1. From the repository root in PowerShell, start the application with `./scripts/dev.ps1 -OpenBrowser` (or `./scripts/dev.ps1` and open `http://127.0.0.1:5173/`). The demo requires an `OPENAI_API_KEY` and a high-spike replay can use API credits.
2. Choose **High spike** and select **Start demo**. The synthetic replay releases three days of events over approximately two minutes after preparing four days of history.
3. Watch the Payments alert card. While its statistical episode is active, it shows **Alert episode active** and **Incident open**. If the detector subsequently observes a quiet window, the card changes to **Alert episode ended** while **Incident open** remains, and the explanation appears below it. The precise ending depends on the actual detector output; replay completion alone does not end an episode.
4. Stop the local services with `./scripts/dev.ps1 -Stop` when finished. No real customer data or email delivery is involved in this replay.

## Checks

- `uv run python -m pytest tests/test_detector_host.py tests/test_incident_worker.py tests/test_api.py -q -p no:cacheprovider`: **11 passed**. These checks cover episode opening, continued alerts, quiet-window ending, new episodes, manual mode, duplicate event redelivery, restart at the handoff, old-database migration, and report/API compatibility.
- `uv run python -m pytest tests -q -p no:cacheprovider`: **69 passed, 8 subtests passed**.
- `npm run build` from `web/`: passed. The UI was not exercised in an interactive browser for this change.

## Deferred

Stage 2 will implement durable worker leases and fencing, bounded retry, and the agreed automatic follow-up cooldown, cap, and coalescing. The current material-evidence follow-up logic still operates during alert updates and does not yet implement those controls. Operator-controlled incident closure and action tracking are Stage 4 work. Replay workers do not currently resume after an API process restart.
