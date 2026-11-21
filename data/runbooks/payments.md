# Payment Authorization Incident Guide

Document ID: RB-PAY-01  
Owner: Payments Operations  
Scope: checkout authorization timeouts, elevated declines, and ambiguous payment outcomes.

## First checks

- Compare authorization request volume, timeout rate, gateway response codes, and p95 latency against the same-length healthy period.
- Determine whether failures are concentrated by gateway, region, payment method, or checkout release.
- Check whether an approved configuration or deployment change overlaps the first measurable service deterioration. Timing alone does not establish cause.
- For uncertain charge outcomes, reconcile the payment processor record with the order record before advising a retry.

## Escalation and remediation

- Page the payments on-call owner when authorization failures are sustained or customers may have duplicate or unknown payment outcomes.
- Follow the change-management process before changing retry settings or rolling back a release.
- Do not increase retries during a gateway timeout without checking duplicate-charge risk and the documented retry budget.

## Verify recovery

- Confirm authorization success and timeout rates return to the service objective for at least two consecutive 30-minute windows.
- Confirm payment-related ticket arrivals fall toward baseline and reconcile any uncertain transactions.

## Evidence to retain

Record exact UTC intervals, gateway response codes, request totals, error totals, change identifiers, and links to affected support tickets.
