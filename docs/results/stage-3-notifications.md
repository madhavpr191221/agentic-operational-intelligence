# Stage 3: Attention and notification lifecycle

## What changed

- Added an independent attention evaluator that assigns each incident **Immediate**, **Review soon**, or **Monitor** without changing detector scores, detector thresholds, count inputs, or investigator behavior.
- Immediate requires either a trusted critical service signal in the latest alert window, or fresh category tickets plus critical telemetry from the distinct `support_tickets` and `product_logs` sources. A report or ticket summary does not count as an additional source. Evidence is bounded to the episode's latest alert window and its sequence/cutoff; later stream events cannot upgrade an earlier episode.
- A completed investigation with relevant cited operational evidence can raise an incident to Review soon. Otherwise it remains Monitor. Attention levels are sticky at the highest level reached; a quiet window or improving metric does not prove resolution.
- Inbox notifications, stable IDs, primary and backup email outbox rows, attempt history, acknowledgement deadlines, acknowledgement records, and one-time escalation state are persisted in SQLite. Acknowledgement and escalation updates are transactional. A late primary acknowledgement supersedes a dispatched backup escalation while preserving both events, including the race where backup delivery is already in flight.
- Simulated email is the default and performs no network call. SMTP is explicitly opt-in with `AOI_EMAIL_MODE=smtp`. SMTP acceptance is recorded as **dispatched**, never as delivered; this implementation has no delivery-receipt integration. Safe known failures receive at most one retry. Ambiguous provider outcomes are recorded as unknown and are not retried blindly.
- Each outbox row persists the adapter mode selected when it was created. If configuration changes before a queued row is sent, the item is blocked instead of allowing simulated mail to become real or silently mislabeling an SMTP send. It can resume when the configured mode is restored.
- The outbox stores a versioned structured email draft alongside its exact subject and body. The draft snapshots the alert facts, evidence cutoff, investigation state and any already-available finding, uncertainty, requested response, recipient, and incident link. It is deterministic and adds no LLM call. Later evidence does not rewrite the saved message.
- The monitor displays a collapsible preview of the exact persisted email for each primary or backup delivery, including recipient, subject, body, and a clear simulated/no-real-email label or SMTP status. Incident links open the matching replay and scroll to its incident card. Immediate notifications can still be acknowledged in the card.
- The API exposes `GET /api/notifications` and `POST /api/notifications/{notification_id}/acknowledge`. Runtime shutdown now stops the notification and investigation workers using FastAPI lifespan cleanup.
- The fixed four-day detector warm-up is cached in the local SQLite database by seed, simulation settings, detector configuration, and cache version. The first run prepares and saves it; later compatible replays hydrate the baseline and start generating the Day 5 stream directly. Cached historical evidence receives fresh IDs per replay, and cache application is transactional and idempotent. The cache contains only generated observable history, count-only detector observations, and detector state; it does not contain evaluator truth.

## MVP defaults and configuration

These are newly adopted, configurable demo defaults. They are not empirically validated operating thresholds or a new detector calibration:

| Setting | Default |
|---|---:|
| Email mode | `simulated` |
| Acknowledgement timeout | 600 seconds (10 minutes of wall-clock time) |
| Trusted synthetic service logs | `true` |
| Payments error-rate critical condition | 10% |
| Other service error-rate critical condition | 10% |
| Orders queue-depth critical condition | Greater than 100 |
| Delivery p95 latency critical condition | At least 1,000 ms |

See `.env.example` for the full settings list. Configure `AOI_PRIMARY_EMAIL`, `AOI_BACKUP_EMAIL`, and `AOI_OPERATOR_DISPLAY_NAME` for the local demo. Real SMTP requires deliberate setup of `AOI_EMAIL_MODE=smtp`, the `AOI_SMTP_*` values, and real recipients. No authentication or identity provider is included; the configured display name is an MVP attribution value, not verified identity.

## See it in the UI

1. Start the application and select a High spike replay. On the first compatible run, the fixed history is prepared and saved. Later runs reuse it and begin streaming on Day 5 without repeating that preparation. The statistical detector and automatic OpenAI investigation run as before.
2. When the episode appears, read its attention card. **Immediate** shows its reason, acknowledgement deadline, email mode/status, and the Acknowledge button. **Review soon** and **Monitor** appear without an urgent email.
3. Acknowledge an Immediate notification. Its card shows the configured operator name and timestamp. If left unacknowledged, the coordinator queues one backup email after the configured wall-clock timeout. The demonstration defaults to simulated delivery and sends no email to a real recipient.
4. Expand **Email preview** to inspect the exact saved message and recipient; simulated mode is explicitly labeled and sends nothing. Expand **Notification history** to inspect creation, dispatch, acknowledgement, and escalation facts. A provider acceptance is not shown as delivery confirmation.

## Checks

Stage 3 automated checks cover source independence, trusted critical signals, ticket-only behavior, report-supported Review soon, unrelated-service evidence, source/cutoff isolation, structured draft persistence and API preview, backup escalation content, simulated dispatch, persistent pending delivery, delivery-mode changes, known-safe versus ambiguous failure, acknowledgement races, and continued ticket ingestion while the adapter is blocked. Tests use synthetic data and local fake adapters; they make no LLM or email calls. Verification: the full backend suite passed (**95 passed, 8 subtests passed**); `npm run build`, `compileall`, and `git diff --check` passed.

## Limits and deferred work

- Thresholds and trusted-source configuration are demo defaults, not validated alerting policies. Source trust and service-specific critical conditions require organization-specific configuration before production use.
- The replay-baseline cache is local to the configured SQLite database. Changing any cache-key input or cache version intentionally causes a cold preparation; deleting the local database also removes the cache.
- There is no provider delivery-receipt webhook or verified **delivered** state. The system records created, dispatched, failed/unknown, acknowledged, and escalated facts that it can observe.
- The configured operator display name is not authenticated. Role-based authorization and identity verification belong to Stage 4.
- Urgency stays at the highest level reached until human incident workflow is implemented. This avoids treating recovery indications or quiet counts as resolution; it is not an incident closure policy.
- The one email adapter is SMTP. No Slack, Teams, paging, or ticketing integration is included. Human actions, recovery descriptions, and authorized incident closure remain Stage 4.
- The policy evaluates the latest alert window and does not automatically downgrade attention or reclassify an incident based on later quiet windows.
