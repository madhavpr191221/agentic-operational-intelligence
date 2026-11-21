---
document_id: historical-postmortems
document_type: postmortem
available_at: 2025-12-01T00:00:00Z
categories: payments,orders,delivery
---
# Historical incident reviews

These fictional reviews describe earlier events. They are useful for generating hypotheses and choosing checks; similarity to a current symptom is not proof that the cause has recurred.

## PM-2025-041: Authorization latency during gateway route saturation

On 2025-09-18, payment authorization timeouts rose while gateway p95 latency and gateway-side 5xx responses increased. Checkout ticket arrivals rose several windows later. Issuer-decline rates stayed near baseline. The investigation linked the event to one saturated gateway route using request traces and gateway response codes. A retry setting had changed the day before, but comparison showed it was not the trigger. The team shifted traffic after confirming the route-level evidence.

## PM-2025-058: Card declines without a payment-service incident

On 2025-10-07, card-decline contacts increased after an issuer risk-policy update. Checkout-payments request volume and latency remained healthy, and gateway timeout rates did not move. Tickets alone initially resembled a payment outage. Issuer response codes separated the decline wave from service degradation. The review recommends checking response-code mix before attributing card-decline contacts to the payment service.

## PM-2025-073: Order status delay from worker capacity

On 2025-11-02, order-processing queue depth and oldest-job age rose before order-status contacts. API acceptance remained healthy, while worker utilization reached its limit. A nearby catalog deployment was unrelated. Worker saturation and delayed status writes established the immediate bottleneck; a later capacity change addressed it.

## PM-2025-089: Stale tracking during a carrier outage

On 2025-11-21, tracking complaints rose while Northstar Carrier API latency and rate-limit responses increased. Delivery-api itself remained available. Comparing carrier response codes with the last successful scan distinguished an external dependency outage from a local adapter failure.
