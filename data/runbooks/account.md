---
runbook_id: account
title: Account access and identity-service checks
categories: account
---
# Account access and identity-service checks

This guide supports investigation and coordination. It does not authorize account changes or identity-provider remediation.

## First checks

- Compare sign-in and verification-code ticket arrivals with timestamped account-api and identity-service request volume, error rate, and p95 latency.
- Separate sign-in failures from profile-save problems. Profile writes and identity verification use different service paths and may have different owners.
- Check whether failures affect all sign-in methods or only one verification channel. Do not infer an identity-provider outage from ticket counts alone.
- Review a time-matched issue or deployment record for the identity-service dependency. A nearby change is a lead to verify, not proof of cause.

## Escalation handoff

- Include the first affected replay window, ticket counts versus expected, relevant error-rate and latency samples, and the evidence cutoff.
- Route identity-verification failures to the identity team; route profile-write failures to customer platform.
- State what remains uncertain and ask the owning team to verify the dependency and customer impact.

## Monitoring after a reported action

- Compare later sign-in ticket counts with identity-service error rate and latency.
- Describe improvement as observed after the reported action; do not claim the action caused it or that every customer's access was restored.
- Keep operational incident closure with an authorized human.
