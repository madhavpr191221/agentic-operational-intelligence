# Stage 2: Investigation queue and evidence scope

## What changed

- Investigation jobs use one persistent FIFO queue. The worker claims one job at a time with a renewable lease and fencing token. Expired jobs and failed attempts receive one retry by default, at the tail of the queue with the same job ID and frozen evidence scope. After the second failed or expired attempt, the job is retained as failed for review. A provider call may have succeeded before a crash, so retries can repeat external work and cost.
- Report completion and revision assignment are atomic. Revisions are assigned in completion order and stay stable even when an earlier job finishes after a retry. Existing completed reports are migrated into the revision table.
- The coordinator records qualifying service-health transitions and relevant issue/change updates as a pending follow-up opportunity. Evidence is assessed at completed-window boundaries, including after a statistical episode ends. Repeated unchanged issue/change records and ordinary count updates do not independently schedule investigations.
- Automatic follow-ups wait for the latest completed investigation and a 10-minute operational cooldown, with at most two per episode by default. Qualifying evidence is coalesced; a follow-up snapshots the latest eligible evidence when it is queued. Manual requests bypass the automatic cap and cooldown. A manual or other completed report clears a pending opportunity only when its snapshot already covers that evidence.
- Event-driven investigations use a fixed cutoff and event sequence, and check source availability. Their focus group is the alert window and up to two preceding windows; the investigator can widen recent searches to six hours and then 24 hours when a material question warrants it. Older persistent records remain separately searchable by relevance.
- The monitor displays the number of running and waiting jobs, the oldest waiting age, and the number of pending follow-up opportunities. An operator can use **Investigate update** after a report completes.

## See it in the UI

1. Start the application from the project root with `./scripts/dev.ps1 -OpenBrowser` and choose a **High spike** demo. The demo requires `OPENAI_API_KEY` and can use API credits.
2. Watch **Alerts and investigations**. The queue line shows running and waiting investigations; each alert card shows the investigation status and its evidence cutoff.
3. After a report completes, **Investigate update** requests a manual investigation. This can use API credits. It remains available even when the automatic follow-up cap is reached.
4. The synthetic replay runs much faster than wall time. The default 10-minute cooldown is real operational time, so a follow-up opportunity may remain pending after the roughly two-minute replay ends. It is not a 10-minute simulated replay interval. Set `AOI_FOLLOWUP_COOLDOWN_SECONDS` before starting the backend if a different demo value is needed; this does not alter the evidence cutoff clock.

Optional environment settings are `AOI_JOB_LEASE_SECONDS` (default 120), `AOI_JOB_MAX_ATTEMPTS` (default 2), `AOI_FOLLOWUP_COOLDOWN_SECONDS` (default 600), and `AOI_MAX_AUTOMATIC_FOLLOWUPS` (default 2). These are configurable MVP defaults, not empirically optimized values.

## Checks

- `uv run python -m pytest -q -p no:cacheprovider`: **79 passed, 8 subtests passed**. Focused queue, detector, API, and agent-tool checks: **22 passed**.
- `npm run build` from `web/`: passed. The UI and live OpenAI path have not been exercised interactively for this stage.
- Tests cover FIFO retry order, expired leases, stale-worker report fencing, stable report revision order, coalescing and cooldown, automatic cap and manual requests, material evidence after episode end, source availability cutoffs, and staged search expansion.

## Limits

The replay clock and source ingestion stop on API restart; persisted investigation jobs recover after their lease expires, but replay itself does not resume. Significance uses the current deterministic service/issue/change heuristics and examines evidence at completed-window boundaries, rather than continuously between boundaries. Human incident closure and the notification/acknowledgement workflow remain later stages. No real OpenAI call or interactive browser session is part of the automated checks.
