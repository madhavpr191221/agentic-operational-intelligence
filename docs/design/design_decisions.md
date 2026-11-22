# Design decisions

This is the concise decision register for the [End-to-End MVP Implementation Plan](agentic_operational_intelligence_end_to_end_design_v2.md). It records adopted scope, demo defaults, deferred TODOs, and remaining configuration details; the end-to-end plan is the governing implementation document.

The sections follow the runtime flow in [the proposed architecture](event-driven-incident-system.md): alert episodes, investigation scheduling, evidence scope and search, then report updates and operator review.

## 1. Alert episodes and grouping

**Terms:** A *window alert* is one category's count being flagged in one 30-minute window. An *alert episode* groups consecutive window alerts for that same category. An episode can contain one alert or many; it has no fixed alert count. For example, `Alert, Alert, Alert, Quiet, Alert` means one three-window episode, then a new one-window episode.

**Current emission behavior:** The runtime writes an `anomaly_alert` event as soon as a window is flagged. There is no consecutive-window confirmation, so one flagged 30-minute window is enough to emit an event. Four consecutive flagged windows currently produce four per-window alert events, while the episode tracker groups them into one episode.

**Proposed event behavior:** Keep the first-window alert for timely detection. Publish one `episode_opened` event on the first flagged window, then `episode_updated` events as the same episode continues, and an `episode_ended` event when the detector's closing rule is met. The coordinator should create one initial investigation for the opened episode, not one per alerting window.

**Event identity and duplicate delivery:** Every emitted event has a stable event ID. All events for the same continuous alert episode share one episode ID. If an event is delivered again after a restart or timeout, the receiver recognizes the event ID and does not create a duplicate incident, episode update, or investigation. A later, separate episode gets a new episode ID. The ID format is an implementation detail; the identity and retry behavior are the design decision.

Example timeline (IDs are illustrative):

| Window just completed | Result | Event ID | Episode ID | Effect |
|---|---|---|---|---|
| 10:00–10:30 | Normal | — | — | No event |
| 10:30–11:00 | Alert | `evt-001` | `pay-2026-0042` | Open one episode and its incident |
| 11:00–11:30 | Alert | `evt-002` | `pay-2026-0042` | Update the existing episode |
| 11:30–12:00 | Alert | `evt-003` | `pay-2026-0042` | Update the existing episode |
| 12:00–12:30 | Normal | `evt-004` | `pay-2026-0042` | End the episode |
| 12:30–13:00 | Alert | `evt-005` | `pay-2026-0043` | Open a new episode and incident |

If `evt-002` is delivered twice, it remains the same event with the same ID; the receiver applies its effect once. The next 30-minute alert is a new event with a new event ID, but it keeps the episode ID while the episode continues. Once that episode ends, a later alert starts a new episode with a new episode ID.

This protects against a failure between receiving an event and saving its effect. For example, the coordinator may begin handling `evt-002`, restart before committing the episode update, and receive `evt-002` again on retry. The receiver must make that retry safe so it cannot create a second incident or investigation.

Requiring two consecutive flagged windows before emitting would add at least 30 minutes of delay and could miss short bursts. Do not add that delay by default; if confirmation is later desired, evaluate its detection delay and missed-incident rate against the current thresholds.

**MVP boundary rule:** The first quiet window ends the statistical episode and finalizes its statistics. It does not prove service recovery, trigger a final investigation by itself, or close the operational incident. Preserve the existing detector configuration; do not add multi-window episode confirmation or a new detector benchmark/calibration project in this MVP.

**Working direction:** Group alerts by category in the operator experience so related episodes are easy to browse. Keep each alert episode identifiable and preserve its own evidence scope and report.

**Default investigation unit:** One alert episode gets one investigation and report. If multiple episodes arrive together, queue separate investigations rather than combining unrelated alerts into one agent context. Category grouping is for browsing; it does not merge episodes or reports.

If multiple categories alert around the same time, investigate their findings separately. Cross-category correlation, deduplication, and joint investigation are deferred TODOs for this MVP. Category grouping or close timing alone is not evidence that alerts share a cause.

**Deferred TODO:** Cross-category correlation, deduplication, and deliberate joint investigations across selected episodes are outside the MVP. Each category keeps its own episodes, investigations, and incident workspaces.

## 2. Investigation modes

**Agreed direction:** Support both automatic and manual investigation.

- In automatic mode, a new alert episode can start an investigation without an operator action.
- In manual mode, alerts wait in the inbox until an operator chooses to investigate.
- Both modes use the same investigation and reporting behavior. The difference is how work starts.

**MVP execution topology:** Use one durable FIFO investigation queue backed by SQLite and one worker. Each episode's initial investigation and each eligible follow-up is a separate job, and only one job runs at a time. Ingestion, count aggregation, detector scoring, episode tracking, and live statistics continue independently while the worker is busy. Alert-window updates alone do not enqueue jobs. Persist stable job IDs, episode IDs, immutable evidence cutoffs/sequence bounds, job states, attempt counts, lease expiries, and fencing tokens. Resume queued jobs in FIFO order; recover expired running jobs with one automatic retry (two attempts total), placed at the FIFO tail under the same job ID and cutoff. Retain exhausted jobs for operator review. The two-attempt limit is a configurable MVP default, not empirically optimized. Enforce one committed report revision per job and fence stale-worker writes. The guarantee is idempotent application state, not exactly-once provider execution or billing. Expose queue depth and oldest waiting-job age. This is a simple MVP capacity setting, not a permanent concurrency limit.

## 3. Evidence cutoff

**Agreed:** An investigation must not use information that became available after its evidence cutoff. A later investigation may use newly available information and create a new report revision while preserving the earlier report.

The cutoff is about when the system could know a record, not only the event time written inside it. A two-week-old issue that was still open and visible at alert time may be relevant; a record imported or created afterward must not appear in the earlier report.

## 4. Evidence sources and lookback

**MVP decision:** Every investigation uses an immutable evidence-availability cutoff. A record is eligible only if it was available to the system by that cutoff, regardless of its event timestamp. Search recent incident evidence in stages, while searching older persistent context separately by relevance.

For recent ticket and service evidence, begin with the alert window and the two preceding 30-minute windows. Expand to six hours, then to at most 24 hours only when the current evidence leaves a material incident question unanswered. Stop earlier if the expansion adds no material evidence. Older unresolved issues, changes, runbooks, and similar persistent records may be retrieved by relevance beyond 24 hours, subject to bounded result/tool limits and the same cutoff. A high retrieval score is not causal evidence.

This is an adopted MVP search policy, not a claim that these ranges are universally optimal. Preserve the cutoff in each report and never let later-arriving or future evidence leak into an earlier revision.

## 5. Progressive search and stopping

The bounded expansion rule is:

1. Search the alert window plus the preceding two windows.
2. If a material question remains, expand recent ticket/service evidence to six hours.
3. If a material question still remains, expand to at most 24 hours.
4. Search older persistent records separately by targeted relevance, not by continually moving the recent time boundary backward.
5. Search for evidence that could contradict the leading explanation as well as evidence that supports it.
6. Stop when expansion adds no material evidence or the configured tool/time/resource limit is reached; state what remains unknown.

The availability cutoff applies at every stage. The 6-hour and 24-hour expansions are only used when justified by a specific unresolved question.

## 6. Follow-up investigations and report revisions

**MVP rule:** The investigation coordinator, not the statistical detector or the language model, decides whether to schedule an automatic follow-up. The configured automatic/manual mode governs initial investigations; manual follow-up requests remain available in either mode.

**Agreed decision — live statistics do not automatically revise investigation reports:** Completed alert windows continue to update the incident's live statistics, including its ticket counts, duration, and peak volume. If the investigation has completed and only these counts have changed, the report remains unchanged, keeps its original evidence cutoff, and no follow-up is queued. A new report revision requires significant new evidence under the rule below or an operator request. This keeps operational monitoring current without spending a language-model run on every count update.

**Working definition of significant:** New information is significant when it became available after the latest report's cutoff, is directly linked to the affected category or service, and could change at least one important report element: the leading explanation, evidence for or against it, affected scope or timeline, an important unknown, or the next verification step.

Examples of new evidence that may meet this definition:

- An operator asks for an updated investigation.
- A relevant issue is opened or its status changes.
- A deployment or configuration change is recorded for the affected service and is plausibly related in time.
- New diagnostic or service-health evidence shows a meaningful change, such as a new error type or recovery evidence.

An alert continuing into another window, a small score fluctuation, another ordinary ticket or log line, a duplicate record, or a new text chunk is not significant on its own. Those items can be included in a later investigation without each one triggering it. The detector can update the alert episode, but only the coordinator applies this follow-up rule.

In automatic mode, eligible triggers can schedule a follow-up. In manual mode, they appear as an update for the operator to review and start. Multiple triggers close together should be coalesced into one follow-up opportunity. If a report is already being prepared, new evidence must not change that report's cutoff; at most one later follow-up can be queued with a newer cutoff.

Each follow-up creates a new report revision and preserves the earlier report. Ordinary log ingestion, new text chunks, or each consecutive alert window should not independently trigger an agent run.

### Where the follow-up lives

A follow-up belongs to the existing alert episode. It is not a new alert and does not belong to the category group itself. The category group helps the operator find an episode; the episode's incident workspace owns its investigation and report history.

- In manual mode, show an update in that workspace, such as "New evidence available since this report," with an operator action to investigate the update.
- In automatic mode, show the follow-up as queued or running there, then make the new report revision available without requiring a click.
- Keep the earlier report available. The new revision should show its evidence cutoff and briefly state what changed since the prior report.

This keeps the operator's action and the resulting explanation attached to the episode they refer to. Joint investigations across episodes are deferred TODOs and are not available in the MVP.

**MVP episode-end decision:** The first quiet window finalizes episode statistics only. It does not trigger a final investigator run. Significant new evidence may still qualify under the existing follow-up rule, and an operator can request a manual follow-up.

**Newly adopted MVP defaults (not previously validated parameters):** Automatic follow-ups have a configurable 10-minute cooldown measured from completion of the most recent investigation and a maximum of two automatic follow-up runs per episode, excluding the initial investigation. Significant qualifying evidence arriving during cooldown is coalesced into one pending opportunity. Manual follow-up requests bypass both cooldown and the automatic cap. These defaults bound demo workload; they are not empirically optimized or presented as universal values.

**Implementation detail:** Represent source links using identifiers/URLs available from each source; exact per-source link rendering can be decided during implementation.

## Detector scope boundary

**MVP decision:** Preserve the current calibrated detector configuration and alerting logic. Record the detector configuration/version used for each alert and retain observed false-alert outcomes when identified by an authorized human. Do not introduce a new statistical benchmarking, threshold-calibration, or detector redesign project before MVP completion; that is a separate future decision.

## 7. Operator action log

**Agreed MVP decision:** An authorized human can record an action taken outside this application and attach it to the existing incident timeline. Preserve what happened, when it reportedly happened, when it was recorded, and who recorded it; also capture the reported performer/team when known. Label the entry as human-reported, not independently verified. The application does not execute remediation.

Show subsequent ticket counts and service-health evidence alongside the recorded action. The system may describe that improvement followed the action, but must not claim the action caused the improvement without supporting evidence. Any follow-up investigation remains governed by the significant-evidence rule in Section 6.

## 8. Recovery observations

**Agreed MVP decision:** Show observed ticket-count trends, available service-health evidence, and human-reported actions on the incident timeline. Use descriptive observations and retain source/time provenance. Do not implement a two-healthy-window recovery classifier or present a deterministic "recovered" verdict in this MVP. The interface may use descriptive labels: Unknown (insufficient post-alert observations), Ongoing (evidence still shows the issue), Improving (observations move toward usual behavior), and Recovery signs (observations are consistent with improvement, without confirming recovery). A decrease in counts or errors does not establish that an action caused the change, prove every customer issue is resolved, trigger an automatic investigation by itself, or close the operational incident. Follow-up investigation remains governed by the significant-evidence rule in Section 6. Operational closure remains an authorized human decision.

## 9. Human attention priority

**Agreed decision:** Keep detection, investigation, and attention priority as separate responsibilities. The detector identifies unusual ticket volume. The investigator explains what may be happening and the evidence for it. A prioritization policy decides whether a person should be interrupted and how soon.

Use three attention levels:

| Level | Meaning | System behavior |
| --- | --- | --- |
| Immediate | Fresh evidence indicates a potentially serious, time-sensitive customer impact. | Notify the responsible team promptly and keep the incident in the inbox. The notification asks for urgent verification; it does not claim the cause is proven or authorize a technical action. |
| Review soon | A credible problem may be present, but its impact or urgency is uncertain or limited. | Place it in a prioritized review queue. |
| Monitor | Activity is unusual, but current evidence does not show a significant operational problem. | Keep it visible in the inbox and continue monitoring. |

An incident may receive **Immediate** priority when either a predefined high-risk condition is met by a fresh, trusted operational signal, or fresh evidence from at least two genuinely distinct source types supports serious ongoing customer impact and delay could worsen it. Distinct sources must not be summaries or transformations of the same underlying data: customer tickets plus an LLM summary of those tickets count as one source, while customer tickets plus independently collected gateway telemetry can count as two. Ticket counts alone do not meet this bar; a ticket total is not necessarily a count of unique affected customers.

The language model may explain the evidence and suggest an attention level, but it cannot by itself trigger a high-priority notification. A predefined policy controls that notification. Uncertainty about a potentially severe issue can still warrant an urgent request to verify the situation. Attention priority may change as new evidence arrives without invoking the investigator for every alert window.

## 10. Immediate notification acknowledgement and escalation

**Agreed MVP decision:** Notification acknowledgement is a separate workflow from investigation and recovery. An incident can be Immediate, acknowledged, still ongoing, and operationally unresolved at the same time. Acknowledgement means a responder has taken responsibility for the response; it never means the incident is resolved.

| Setting or event | MVP behavior |
| --- | --- |
| Primary recipient | The configured responsible team. |
| Acknowledgement timeout | 10 minutes as a configurable demo default, not an industry-wide standard. |
| No acknowledgement at timeout | Send one escalation to the configured backup team. |
| No backup team configured | Mark escalation unavailable and retain a visible warning. |
| Acknowledgement before escalation | Cancel the pending escalation. |
| Acknowledgement after backup escalation | The first valid acknowledgement establishes response ownership. Mark the backup escalation superseded, retain its history, and inform the backup team of the ownership change when the notification channel supports it. |
| Repeated escalation | Do not send notifications indefinitely. If no one acknowledges, keep the unacknowledged state and a visible warning. |

Record each notification delivery attempt separately from acknowledgement. A sent or delivered notification does not prove that a person received, read, or accepted it. Keep the notification history attached to the incident.

The MVP uses the in-app incident/notification inbox plus one email adapter. Simulated email is the default for synthetic replay and performs no network delivery; real email is opt-in through configuration. Do not add other external integrations. Persist the inbox item and stable notification ID with its durable outbox row atomically before dispatch, append every delivery attempt, and reuse the ID and provider idempotency key where supported. If a send outcome is ambiguous and the provider cannot guarantee deduplication, record uncertainty and require review rather than blindly retrying or claiming exactly-once email. Track **created**, **dispatched**, **delivered** (only when verifiable), **acknowledged**, and **escalated** as separate timestamped facts. If email dispatch fails, retain the incident and notification in the in-app inbox and record the failure. Persist the 10-minute acknowledgement deadline at notification creation so restart does not reset it. The escalation scheduler transactionally checks acknowledgement state before claiming the single backup escalation. A late primary acknowledgement supersedes that escalation while preserving both histories. Do not retry or escalate indefinitely. Email retries/failures do not block ticket ingestion or detector transactions.

## 11. Product persona and boundary

**Primary persona:** Support Operations Lead / Incident Coordinator. **Secondary personas:** Engineering Responder / Service Owner, Frontline Support Agent, System Administrator / Operations Manager. This is an operational intelligence and incident coordination tool, not a ticket-handling system. It observes ticket activity and operational evidence, prepares investigations and notifications, and tracks response context; source ticket handling remains in its existing system.

Operational incident closure is a separate, authorized human decision. Statistical episode closure, notification acknowledgement, and improving monitoring observations do not close an operational incident.

**MVP multi-category rule:** Monitor categories independently and create separate episodes, investigations, and incident workspaces. Cross-category correlation, deduplication, and joint investigations are explicitly deferred TODOs.
