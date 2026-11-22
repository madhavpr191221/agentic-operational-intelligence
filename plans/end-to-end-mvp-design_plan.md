# End-to-end MVP implementation plan

The governing product scope and acceptance criteria are in the [end-to-end MVP design](../docs/design/agentic_operational_intelligence_end_to_end_design_v2.md). The [design decision register](../docs/design/design_decisions.md) and [queue lifecycle notes](../docs/design/investigation-queue-lifecycle.md) provide supporting detail. This plan translates the design into reviewable implementation stages; it does not imply that proposed functionality already exists.

## Goal and current starting point

Deliver the complete operator workflow: detection, evidence-backed investigation, attention and notification, human coordination, continued monitoring, and authorized incident closure. The primary user is a Support Operations Lead or Incident Coordinator.

The repository already contains synthetic live replay, the existing count-only detector and episode tracker, SQLite incident records and jobs, an investigation worker, evidence retrieval, and a monitor UI. A preliminary code review identified important gaps: episode end currently queues a final investigation; interrupted running jobs are blocked rather than recovered under a bounded lease policy; follow-ups lack the agreed cooldown and cap; and investigator instructions still describe an optional later focus window. Urgency, notification, acknowledgement, human action, and closure workflows need to be implemented and verified.

Preserve the current detector configuration and scoring. Do not add a statistical recalibration project, automated remediation, individual customer messaging, cross-category correlation or joint investigation, or additional external notification integrations. Each stage should leave the application usable and end with a reviewable completion gate.

## Stage 1: Episode events and incident contracts

**Goal:** A completed 30-minute window produces the correct durable per-category episode and incident state.

1. Inspect the existing detector host, episode tracker, outbox, incident registry, API, and relevant tests before changing their contracts.
2. Preserve the existing detector configuration, scores, thresholds, and count-only inputs. Continue assigning stable event IDs and episode IDs.
3. On episode opening, create one incident workspace and, in automatic mode, one initial investigation job. Manual mode waits for an operator request.
4. Apply subsequent alert windows to the same episode's statistics; an additional alert window alone does not schedule a new investigation.
5. On the first quiet window, finalize statistical episode state and statistics. Do not schedule a final investigation solely because the episode ended, and do not close the operational incident.
6. Represent statistical episode state and operational incident state separately. Preserve existing records and reports through an additive, repeatable SQLite migration; keep API/UI compatibility where possible. Full human closure is delivered in Stage 4.
7. Make duplicate event delivery safe with transactional application and stable identities. Preserve correct category and stream isolation. Retain existing significant-evidence follow-up behavior only insofar as it does not contradict the frozen episode-end rule; Stage 2 completes the follow-up policy.

**Completion gate:** Verify unchanged detector outputs for representative count sequences; opening, repeated alerts, first quiet window, and a later separate episode; one incident and initial job per episode; safe duplicate delivery and restart at the event handoff; no final job from a quiet window alone; independent categories; and existing database migration/API compatibility. Run focused deterministic tests and the applicable regression checks. Record anything not implemented in this stage.

## Stage 2: Durable investigation execution and evidence scope

**Goal:** Investigations run from one durable FIFO queue while monitoring continues.

- Persist stable job identity, FIFO enqueue order, state, evidence cutoff and event-sequence bound, attempt count, lease expiry, and fencing token. One worker runs at most one investigation at a time.
- Recover expired running jobs using one automatic retry by default (two attempts total), at the FIFO tail with the same job ID and frozen scope. Retain exhausted jobs for operator review. Fence stale worker commits and enforce one committed report per job. External LLM execution and billing cannot be guaranteed exactly once.
- Expose queue depth and oldest waiting-job age. Keep ingestion, aggregation, detection, notification dispatch, and acknowledgement scheduling independent from slow investigations.
- Start recent evidence with the alert window and two preceding windows; expand to six hours and then 24 hours only when justified. Retrieve older persistent issues, changes, runbooks, and references separately by relevance, subject to the availability cutoff. Remove stale future-window instructions and enforce cutoffs across every evidence path.
- Implement the existing significance rule for follow-ups. Default to a configurable 10-minute cooldown from the latest investigation completion and at most two automatic follow-ups per episode, excluding the initial investigation. Coalesce qualifying evidence during cooldown into one pending opportunity; allow manual follow-ups despite the automatic cooldown/cap. Freeze the eventual job snapshot without changing running investigations.

**Completion gate:** Verify FIFO, snapshot isolation, category scope, no future evidence, concurrent monitoring, queued/running restart, lease expiry, stale-worker fencing, bounded retry, one committed report, coalescing, cooldown, cap, and manual follow-ups using deterministic doubles. No paid API calls in automated tests.

## Stage 3: Attention, notifications, and acknowledgement

**Goal:** Identify incidents requiring human attention and preserve their notification history.

- Implement Immediate, Review soon, and Monitor using explicit configurable policy. Immediate requires a fresh trusted predefined critical signal or plausible serious impact corroborated by fresh, genuinely independent underlying sources, with time-sensitive consequences. A ticket summary and its underlying tickets are one source.
- Persist the in-app notification and durable email outbox row atomically; preserve a stable notification ID and delivery-attempt history. Use a single email adapter, with simulated delivery as the default for synthetic replay and no network email request in simulated mode. Real email is explicitly configured.
- Keep created, dispatched, delivered when verifiable, acknowledged, and escalated as distinct facts. Failed or uncertain delivery cannot erase the inbox item. Retry ambiguous sends only when the outcome or provider idempotency makes it safe.
- Persist a configurable acknowledgement deadline, default 10 minutes for the demo. Transactionally check acknowledgement before one backup escalation. No endless loop; late primary acknowledgement supersedes backup escalation while preserving history.

**Completion gate:** Verify policy classifications and source independence; simulated no-network delivery; failed and ambiguous dispatch; restart with pending delivery and just before deadline; acknowledgement before claim and a race with escalation; and ongoing ingestion while delivery is slow.

## Stage 4: Operator workspace and human actions

**Goal:** Enable the primary user to understand, coordinate, and track an incident.

- Present an operational overview and incident inbox while retaining access to the live ticket monitor. Show episode statistics and state, investigation status and revisions, evidence cutoff, finding and uncertainty, urgency rationale, notification history, and a timeline.
- Allow an authorized human to acknowledge and record an action taken outside the application, including what happened, when, and who recorded it; include performer/team when known. Label actions human-reported. The application does not execute remediation.
- Show later ticket trends, available service-health evidence, and reported actions together. Use descriptive states such as Unknown, Ongoing, Improving, and Recovery signs without a two-healthy-window classifier or automatic causal attribution.
- Support authorized human incident closure with a reason and supporting context. Acknowledgement, quiet windows, and recovery observations do not automatically close the incident.

**Completion gate:** Verify the full operator workflow and state distinctions in the UI and API, including immutable report history, authorization behavior, correct action provenance, and absence of automatic closure or unsupported causal claims.

## Stage 5: End-to-end demonstration and resilience

**Goal:** Demonstrate the complete MVP as one observable system.

Run a synthetic Payments scenario through ticket arrival, a completed alerting window, episode and incident creation, an evidence-bounded investigation, urgency assessment, inbox notification and simulated email, human acknowledgement, action recording, subsequent monitoring observations, and authorized closure. Exercise independent concurrent categories and preserve separate episodes, investigations, and workspaces.

**Completion gate:** Run the acceptance scenario and restart/race matrix from the master design: queued and running jobs, after report commit, pending delivery, immediately before acknowledgement deadline, and acknowledgement/escalation races. Verify no duplicate committed effects, no network email in simulated mode, and continued monitoring while investigation or delivery is slow. Run appropriate backend tests and the frontend build; keep automated checks deterministic and free of paid calls.

## Time and demo semantics

Synthetic replay advances simulated incident time faster than wall-clock time. Evidence cutoffs and event windows use simulated/source time and evidence availability; leases, acknowledgement deadlines, and operational cooldowns require an explicit operational clock. Keep those clocks distinct and injectable for deterministic checks. Pausing replay must not silently advance its simulated evidence cutoff. Document the demo's timing behavior so a 10-minute operational timeout is not mistaken for 10 minutes of accelerated replay.

## Review sequence

Start Stage 1 on a dedicated implementation branch based on the frozen design and this plan. Review each stage's behavior and completion gate before proceeding. If integration reveals a material conflict with the master design, make it visible and resolve it before changing the frozen product scope.
