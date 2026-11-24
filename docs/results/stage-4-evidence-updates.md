# Stage 4: Later evidence updates

## Delivered

- A later alert in another category still opens its own incident and initial investigation immediately. Co-occurrence alone does not create an extra job for an existing incident.
- Once an evidence-backed relationship connects an active incident to a specific shared service, later service, issue, or change evidence for that service can qualify as material evidence for each linked active episode.
- Existing follow-up safeguards remain in force: one pending update per incident coalesces to the latest cutoff, the investigation worker applies its existing cooldown and episode cap, and each report keeps its immutable evidence cutoff.
- A first quiet window now only ends the statistical episode. It does not queue a follow-up or final investigation by itself. Monitoring evidence after episode end does not schedule an automatic run.
- The incident workspace continues to show each report revision and replay evidence cutoff. Revisions after the initial report explain why the report was refreshed.

```mermaid
sequenceDiagram
    participant M as Payments monitoring
    participant R as Evidence-backed relationship
    participant C as Orders monitoring
    participant F as Follow-up policy
    participant Q as Investigator queue
    participant W as Payments workspace
    M->>R: Payments and Orders share a cited service
    C->>R: Later shared-service evidence changes materially
    R->>F: Offer update to each linked active episode
    F->>F: Coalesce by incident; enforce cooldown and episode cap
    alt Eligible now
        F->>Q: Queue one bounded follow-up per eligible incident
        Q->>W: Append report revision at a later evidence cutoff
    else Cooling down or cap reached
        F->>F: Keep latest significant evidence pending
    end
    Note over Q,W: Quiet-window episode closure alone creates no final run
```

## Verification

- `uv run pytest tests/test_detector_host.py tests/test_incident_worker.py tests/test_incident_workspace.py tests/test_agent_tools.py -q --tb=short` - 29 passed.
- The tests cover linked shared-service degradation reaching active incidents, ended episodes not receiving final runs, and existing follow-up coalescing and limits.
- Earlier in this implementation branch, `npm run build` passed after the Stage 3 UI change. The frontend build will be rerun after the Stage 5 UI work.
- `git diff --check` - passed.

## Limits

- Only evidence tied to a relationship already supported by underlying records can fan out as a relevant update. A later alert or similar count pattern alone does not.
- This does not add a new investigator call policy or change the existing cooldown and per-episode follow-up cap.
- Ended episodes do not receive automatic investigations; the operational incident remains open until a human resolves it.
