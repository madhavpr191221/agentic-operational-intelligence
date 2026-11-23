# Stage 4: Operator incident workflow

## What changed

- The application opens to one operator incident screen. Its default Open/Resolved queues are scoped to the latest synthetic replay; Replay history exposes records from older runs without mixing them into the current queue.
- Every detector-opened episode appears in the inbox before its investigation completes. Alert windows update that same row; a later episode gets its own row. Rows show alert ticket totals versus expected, episode state, investigation progress, and urgency when assessed.
- Investigation progress is shown independently from urgency. Before an urgency assessment exists, the UI says `Assessment pending`; it does not label the incident Monitor or show an invented cause. Findings, evidence, uncertainty, and recommendations come from saved investigation reports.
- The incident workspace presents statistical episode state separately from operational incident state, episode alert statistics, attention rationale, investigation revisions and evidence cutoffs, reported actions, lifecycle history, and later monitoring observations.
- Immediate notifications can be acknowledged from the inbox workspace. Acknowledgement remains distinct from operational resolution.
- Operators can add an action to an open incident with the reported action time, optional performer/team and reference, and server-recorded time and configured recorder name. Entries are labeled `operator_reported`; the application does not execute or verify remediation. Repeated requests using the same idempotency key return the original entry, and reusing a key with different content is rejected.
- Incident closure requires a reason and may include supporting context. Reopening requires a reason. Both transitions and their actor/time are appended to the timeline. Action entry is disabled while resolved; reopen the incident before adding a later action.
- Resolving any priority moves the item out of Open and into Resolved. Reopening returns it to Open and preserves the prior closure history.
- The workspace shows post-episode ticket windows against the usual count and category-matching service-health summaries. Its Unknown, Ongoing, Improving, and Recovery signs labels are descriptive cues only; they do not establish causality, a verified recovery, or automatic closure. It uses no two-healthy-window classifier.
- Replay events and app activity are shown in separate timeline sections. Replay timestamps are synthetic event time in UTC; queue, report-completion, notification, acknowledgement, and operator-record timestamps are labeled as app time with the browser's timezone.
- The incident-list API projection now includes per-episode ticket totals and the latest investigation job state so the queue can be useful without loading every detail report.
- SQLite setup is additive and repeatable. New action and lifecycle history is append-only; the existing episode status and reports are preserved.

## Local identity and authorization boundary

The MVP uses `AOI_OPERATOR_DISPLAY_NAME` (default `Local operator`) to attribute acknowledgement, action, closure, and reopening records. This is a configured local demo identity, not verified authentication or role-based authorization. The interface states this explicitly. Do not expose this local setup as production authorization.

## API

- `GET /api/incidents` lists incident workspaces.
- `GET /api/incidents/{incident_id}` returns episode statistics, investigation reports/revisions, action and lifecycle history, and subsequent monitoring observations.
- `POST /api/incidents/{incident_id}/actions` records a human-reported action. Requires description, timezone-aware occurrence time, and idempotency key; performer and reference are optional.
- `POST /api/incidents/{incident_id}/close` resolves an open incident with a required reason and optional context.
- `POST /api/incidents/{incident_id}/reopen` reopens a resolved incident with a required reason.

## See it in the UI

1. Start with `./scripts/dev.ps1 -OpenBrowser`. The default view is **Incident inbox**. The latest replay is selected by default. Start a replay in Live monitor; each opened alert episode appears in Open while its investigation is queued or running, then the same item gains the saved report.
2. Select **Replay history** to browse older synthetic runs. Each history item identifies its replay and distinguishes replay/event time from app-recorded time.
3. Acknowledge an Immediate notification in its incident workspace. The record shows who acknowledged it and app time.
4. On an open incident, record an externally performed action. The entry shows the reported action time and the separate time and identity under which it was recorded.
5. Resolve an incident with a reason and optional context. It leaves Open and appears under Resolved regardless of priority. Reopen it with a reason if later information changes the decision; the earlier closure remains in the timeline.
6. Select **Live monitor** to return to seeded ticket replay and detector state.

## Checks

- Focused detector, incident workspace, and API tests: **16 passed**.
- Full backend suite: **100 passed, 8 subtests passed**.
- `npm run build` from `web/`: passed.
- `uv run python -m compileall -q src/agentic_ops`: passed.
- `git diff --check`: passed (Git emitted only its existing LF/CRLF working-copy notices).

## Limits

- There is no authentication, verified actor identity, or role-based access control. The configured operator name is attribution only.
- Recovery labels use currently available seeded counts and matching service summaries as descriptive UI cues. They are not a formal recovery classifier or production policy.
- No remediation, ticket-system integration, cross-category correlation, or customer messaging was added.
