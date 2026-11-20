# Carrier Integration Incident Guide

Document ID: RB-DEL-01  
Owner: Delivery Platform Operations  
Scope: carrier API timeouts, rate limits, missing tracking updates, and delayed delivery estimates.

## First checks

- Compare carrier request volume, timeout and rate-limit responses, p95 latency, and tracking-update lag with the same-length healthy period.
- Check whether the issue is limited to one carrier, endpoint, region, or credential set.
- Inspect dependency status and approved integration changes. A nearby change is not causal evidence by itself.
- Distinguish a carrier lookup failure from a warehouse dispatch delay using shipment scan events.

## Escalation and remediation

- Escalate sustained carrier failures to the delivery integration owner and the carrier support contact.
- Use an alternate carrier or fallback only when the dispatch runbook and business rules permit it.
- Avoid aggressive retries when rate limits are elevated; use the documented backoff policy.

## Verify recovery

- Confirm carrier success rate and latency return to objective and tracking updates resume.
- Confirm delivery-related ticket arrivals fall toward baseline for at least two consecutive 30-minute windows.

## Evidence to retain

Record carrier name, UTC interval, endpoint, response codes, latency, retry behavior, shipment scan gaps, change identifiers, and representative ticket IDs.
