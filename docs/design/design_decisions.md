# Design decisions

This is a living record of product and architecture decisions for the incident detection and investigation system. It captures discussion outcomes and open questions; it is not an implementation plan.

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

**Current boundary rule:** One quiet window ends the current episode grouping. This means a brief dip can split a continuing service problem into two statistical episodes. Ending the grouping does not prove service recovery. Whether the detector should require multiple quiet windows before ending an episode remains open for evaluation.

**Working direction:** Group alerts by category in the operator experience so related episodes are easy to browse. Keep each alert episode identifiable and preserve its own evidence scope and report.

**Default investigation unit:** One alert episode gets one investigation and report. If multiple episodes arrive together, queue separate investigations rather than combining unrelated alerts into one agent context. Category grouping is for browsing; it does not merge episodes or reports.

If multiple categories alert around the same time, investigate their findings separately first. For example, a Payments report may find authorization errors while an Orders report finds delayed confirmations. The system may then offer a shared-cause explanation, such as a checkout service change, only if evidence from both categories supports that connection. Category grouping or close timing alone is not evidence that the alerts share a cause.

**Open question:** Whether operators should also be able to request a deliberate joint investigation across selected episodes, in addition to category-based browsing.

## 2. Investigation modes

**Agreed direction:** Support both automatic and manual investigation.

- In automatic mode, a new alert episode can start an investigation without an operator action.
- In manual mode, alerts wait in the inbox until an operator chooses to investigate.
- Both modes use the same investigation and reporting behavior. The difference is how work starts.

## 3. Evidence cutoff

**Agreed:** An investigation must not use information that became available after its evidence cutoff. A later investigation may use newly available information and create a new report revision while preserving the earlier report.

The cutoff is about when the system could know a record, not only the event time written inside it. A two-week-old issue that was still open and visible at alert time may be relevant; a record imported or created afterward must not appear in the earlier report.

## 4. Evidence sources and lookback

**Open question:** There is no agreed universal lookback duration. A fixed two-hour search could miss an older unresolved bug or change that contributes to today's incident.

**Working direction:** Separate two kinds of evidence rather than forcing them into one time range:

1. **Recent incident context** — a bounded period around the alert for ticket volume, customer themes, and service behavior. The duration still needs to be chosen and evaluated.
2. **Longer-lived background evidence** — older issues, changes, runbooks, and other records that were available by the cutoff. Retrieve these by status and relevance, and label them as older context.

For example, an alert at 14:00 could compare recent ticket themes and service health while also retrieving a relevant issue opened two weeks earlier that remains unresolved. The report should distinguish the issue's age from when it was observed during the investigation.

## 5. Progressive search and stopping

**Working proposal:** Start with the alert and nearby windows, then widen the search when the evidence leaves important questions unanswered. Do not rely on one fixed lookback for every source.

1. Start with the alert window and the previous two 30-minute windows.
2. If timing or symptoms remain unclear, widen recent ticket and service evidence to six hours, then at most 24 hours. Do not expand this time range again for that report.
3. Separately search older, longer-lived records such as unresolved issues, relevant changes, and runbooks. These may predate the 24-hour range, but must have been available by the investigation cutoff. Retrieve only a bounded, ranked set relevant to the incident questions; do not scan the archive chronologically.

“Most relevant” should mean relevant to a specific incident question or candidate explanation, within the allowed time and source scope. Search ranking can help find records, but a high ranking is not proof of causation. The investigator should look for supporting and contradictory evidence across sources.

**Hard stopping rule:** Recent evidence gets no more than two expansions (two hours to six hours, then to 24 hours). Older persistent records are searched by targeted retrieval with a fixed result limit, not by continually moving the date boundary backward. Stop earlier if an expansion finds no material new evidence. In all cases, stop at the investigation's tool, time, or resource limit and report remaining unknowns instead of searching indefinitely.

These time ranges and result limits are candidate defaults to evaluate, not statistically established values. The key decision is the bounded, staged search; the exact limits can change based on realistic incident tests.

### When to stop within those limits

At each stage, the investigator should:

1. State the leading explanation and the important evidence gap it still has.
2. Search for evidence that could support the explanation, then make a targeted search for evidence against it or for a credible alternative.
3. Decide whether the new results materially change the evidence or the next question.

Stop widening when a broader search adds no material evidence. Stop in all cases at the time, tool, or resource limit. The investigator does not need to prove a cause before stopping: it should report a likely explanation when supported, or say the evidence is insufficient and name what remains unknown. Stopping the search is not proof that the leading explanation is true, and a fixed number of citations alone is not a measure of evidence quality.

## 6. Follow-up investigations and report revisions

**Working proposal:** The investigation coordinator, not the statistical detector or the language model, decides whether to schedule a follow-up. The configured mode applies to both the first investigation and follow-ups unless a separate control is later justified.

**Working definition of significant:** New information is significant when it became available after the latest report's cutoff, is directly linked to the affected category or service, and could change at least one important report element: the leading explanation, evidence for or against it, affected scope or timeline, an important unknown, or the next verification step.

Candidate automatic triggers that meet this definition:

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

This keeps the operator's action and the resulting explanation attached to the episode they refer to. A joint investigation across episodes remains a separate, explicit action, not the normal follow-up flow.

**Open questions:** How should direct links to a category or service be represented for each source? Should an episode ending trigger a final agent update, or only a status and totals refresh? What cooldown and maximum follow-up count should apply? These need to be evaluated before implementation.

## Deferred work: detector operating point

**TODO (outside this branch's scope):** Evaluate the tradeoff between false alert episodes and missed or late incident detection. Calibrate the complete alert rule against a stated false-episode budget, then measure incident detection rate and delay on held-out healthy controls and incident scenarios. Report results by scenario family; do not change detector thresholds as part of the event-driven architecture work.
