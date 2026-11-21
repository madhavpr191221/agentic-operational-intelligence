# Order Processing Queue Incident Guide

Document ID: RB-ORD-01  
Owner: Order Platform Operations  
Scope: growing order-worker queues, delayed confirmations, and stale order status.

## First checks

- Compare queue depth, oldest job age, worker throughput, retry count, and order creation rate over time.
- Separate delayed processing from failed checkout or payment authorization. Verify whether an order record was created before asking a customer to resubmit.
- Check worker health, downstream dependency latency, and recent changes to queue consumers or message schemas.
- Compare the affected queue with a healthy region or unaffected order type.

## Escalation and remediation

- Escalate to the order-platform on-call owner when queue age exceeds the service objective or continues to grow.
- Follow the queue recovery procedure before replaying or draining messages; protect against duplicate order creation.
- Treat a nearby deployment or configuration change as a lead to verify, not as proof of cause.

## Verify recovery

- Confirm queue depth and oldest-job age are falling, workers are making progress, and new orders reach a terminal state.
- Confirm order-status contacts return toward baseline for at least two consecutive 30-minute windows.

## Evidence to retain

Record queue name, UTC interval, queue depth, oldest-job age, worker throughput, duplicate safeguards, change identifiers, and representative ticket IDs.
