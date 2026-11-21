---
document_id: service-catalog
document_type: service_guide
available_at: 2025-12-01T00:00:00Z
categories: payments,orders,account,delivery
---
# Service catalog and dependency guide

This fictional catalog describes service ownership and normal request paths. It is reference material, not a live health feed. Confirm current behavior using timestamped service logs and incident records.

## Payments: checkout and authorization

The checkout flow calls `checkout-api`, which calls `checkout-payments`. The payment service sends authorization requests to the Northstar Gateway and then to the card issuer. The payments team owns checkout-payments; the platform payments rotation owns gateway configuration. Useful signals include authorization success rate, timeout rate, gateway response codes, and retries per request. A customer report that a card was declined does not by itself distinguish an issuer decline from a timeout or checkout defect.

## Orders: accepted orders and worker queue

`orders-api` accepts completed checkouts and places order work on the `order-processing` queue. `order-workers` consume queued jobs and write status updates. The commerce operations team owns the API and worker pool. Useful signals include queue depth, oldest job age, worker concurrency, database write latency, and the fraction of orders whose status advances. A growing queue suggests delayed processing but does not identify why workers are behind.

## Delivery: tracking and carrier dependency

`delivery-api` requests tracking updates from the Northstar Carrier API. The fulfillment integrations team owns the adapter; the carrier owns its external service. Useful signals include carrier request errors, p95 latency, rate-limit responses, and age of the last successful scan. Stale tracking can also result from a carrier having no new scan, so compare API health with carrier responses.

## Account: profile and identity

`account-api` writes profile changes and checks identity state through the identity service. The identity team owns authentication; customer platform owns profile writes. Useful signals include write failures, identity-service latency, and profile update retries. A failed profile save can be a client validation issue even when both services are healthy.
