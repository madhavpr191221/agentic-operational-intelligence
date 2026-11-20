"""Seeded stochastic generator for a fictional product and support system."""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from agentic_ops.domain import CATEGORIES, SimulationConfig

WINDOW_MINUTES = 30
SIMULATION_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
PAYMENT_INCIDENT_FAILURE_PROBABILITY = 0.26
INCIDENT_CATEGORY = {
    "payment_issue": "payments",
    "orders_queue_backlog": "orders",
    "delivery_carrier_degradation": "delivery",
}


@dataclass
class GeneratedEvent:
    simulated_at: str
    source: str
    event_type: str
    payload: dict[str, Any]


@dataclass
class GeneratedWindow:
    index: int
    start: str
    end: str
    ticket_count: int
    category_counts: dict[str, int]
    events: list[GeneratedEvent]


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth sampler; the means in this simulator are intentionally modest."""
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    product = 1.0
    count = 0
    while product > limit:
        count += 1
        product *= rng.random()
    return count - 1


def _negative_binomial(rng: random.Random, mean: float, shape: float) -> int:
    """Gamma-Poisson mixture with mean=mean and dispersion controlled by shape."""
    if mean <= 0:
        return 0
    latent_mean = rng.gammavariate(shape, mean / shape)
    return _poisson(rng, latent_mean)


def _binomial(rng: random.Random, trials: int, probability: float) -> int:
    return sum(rng.random() < probability for _ in range(max(0, trials)))


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def generate_run(config: SimulationConfig, include_ticket_events: bool = True) -> Iterator[GeneratedWindow]:
    """Generate 72 simulated hours in 30-minute windows.

    The first day is a baseline. Incident scenarios plant a late increase in one
    category, with corresponding but independently generated text and service
    evidence. Only the per-category counts are passed to detection.
    """
    if config.window_count < 4:
        raise ValueError("window_count must be at least 4")
    if config.calibration_windows < 1 or config.calibration_windows >= config.window_count:
        raise ValueError("calibration_windows must be between 1 and window_count - 1")
    if not 0.0 <= config.contact_probability_per_failure <= 1.0:
        raise ValueError("contact_probability_per_failure must be between 0 and 1")
    if len(config.baseline_category_means) != len(CATEGORIES) or any(
        not math.isfinite(value) or value < 0 for value in config.baseline_category_means
    ):
        raise ValueError("baseline_category_means must provide one finite nonnegative rate per category")

    rng = random.Random(config.seed)
    ticket_rng = random.Random(config.seed ^ 0x1D2C3B4A)
    evidence_rng = random.Random(config.seed ^ 0x6E88A11)
    # Keep message wording randomness separate so text generation never changes
    # the seeded count process or its detector inputs.
    message_rng = random.Random(config.seed ^ 0x5A17C9E3)
    start = SIMULATION_START
    issue_window = min(config.calibration_windows, config.window_count // 3)
    resolved_window = min(issue_window + 12, config.window_count - 1)
    scenario_category = INCIDENT_CATEGORY.get(config.scenario)
    issue_key = {
        "payment_issue": "PAY-204", "orders_queue_backlog": "ORD-318",
        "delivery_carrier_degradation": "DEL-092", "resolved_issue": "PAY-205",
    }.get(config.scenario, "CAT-118")
    issue_details = {
        "payment_issue": ("Payment authorization timeouts", "checkout-payments",
                          "Customers report authorization timeouts; gateway responses are delayed."),
        "orders_queue_backlog": ("Order processing queue is growing", "orders-api",
                                 "Order workers are falling behind and order status is delayed."),
        "delivery_carrier_degradation": ("Carrier API requests are timing out", "delivery-api",
                                         "Carrier status requests are slow or rate limited."),
        "resolved_issue": ("Payment authorization timeouts", "checkout-payments",
                           "Intermittent authorization failures reported."),
    }

    for index in range(config.window_count):
        window_start = start + timedelta(minutes=WINDOW_MINUTES * index)
        window_end = window_start + timedelta(minutes=WINDOW_MINUTES)
        events: list[GeneratedEvent] = []

        if config.scenario in issue_details and index == issue_window:
            issue_title, issue_service, issue_description = issue_details[config.scenario]
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start), source="issue_tracker", event_type="issue_created",
                payload={"issue_key": issue_key, "title": issue_title, "status": "open",
                         "service": issue_service, "description": issue_description,
                         "project": "OPS", "priority": "P2"},
            ))
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(hours=1)), source="issue_tracker", event_type="issue_updated",
                payload={"issue_key": issue_key, "title": issue_title, "status": "investigating",
                         "service": issue_service, "update": "Owner is collecting time-aligned diagnostics."},
            ))
        if scenario_category and index == max(issue_window, config.window_count - 5):
            # A temporally nearby but unrelated change is a deliberate distractor.
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start - timedelta(minutes=15)), source="change_log", event_type="change_record",
                payload={"change_key": "CHG-889", "service": "catalog-search",
                         "summary": "Search cache parameter adjusted", "outcome": "monitoring_required",
                         "author": "platform-team"},
            ))
        if config.scenario in ("payment_issue", "resolved_issue") and index == issue_window:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(minutes=2)), source="change_log", event_type="change_record",
                payload={"change_key": "CHG-771", "service": "checkout-payments", "summary": "Gateway retry configuration updated",
                         "outcome": "monitoring_required"},
            ))
        if config.scenario == "unrelated_issue" and index == issue_window:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start), source="issue_tracker", event_type="issue_created",
                payload={"issue_key": issue_key, "title": "Search suggestions load slowly", "status": "open",
                         "service": "catalog-search", "description": "A small number of search requests time out."},
            ))
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(minutes=2)), source="change_log", event_type="change_record",
                payload={"change_key": "CHG-772", "service": "catalog-search", "summary": "Search cache tuning",
                         "outcome": "monitoring_required"},
            ))
        if config.scenario == "unrelated_issue" and index == max(0, config.window_count - 5):
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(minutes=1)), source="change_log", event_type="change_record",
                payload={"change_key": "CHG-889", "service": "catalog-search",
                         "summary": "Search cache parameter adjusted", "outcome": "monitoring_required"},
            ))
        if config.scenario == "resolved_issue" and index == resolved_window:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start), source="issue_tracker", event_type="issue_resolved",
                payload={"issue_key": issue_key, "title": "Payment authorization timeouts", "status": "resolved",
                         "service": "checkout-payments", "resolution": "Retry configuration rolled back."},
            ))

        attempts = _poisson(rng, config.payment_attempts_per_window)
        failure_probability = 0.012
        if config.scenario == "payment_issue" and index >= config.window_count - 4:
            failure_probability = PAYMENT_INCIDENT_FAILURE_PROBABILITY
        failed_attempts = _binomial(rng, attempts, failure_probability)

        if config.scenario == "unrelated_issue" and index >= config.window_count - 4:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(minutes=4)), source="product_logs", event_type="service_log_summary",
                payload={"service": "catalog-search", "requests": attempts, "errors": max(1, _poisson(evidence_rng, 8.0)),
                         "level": "warning", "message": "Search suggestion latency elevated."},
            ))
        else:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(minutes=4)), source="product_logs", event_type="service_log_summary",
                payload={"service": "checkout-payments", "requests": attempts, "errors": failed_attempts,
                         "failure_probability": failure_probability, "level": "error" if failed_attempts else "info",
                         "message": "Payment authorization timeout observed." if failed_attempts else "Payment requests healthy."},
            ))

        orders_requests = 500 + _poisson(evidence_rng, 40.0)
        orders_errors = _poisson(evidence_rng, 3.0)
        orders_queue_depth = max(0, int(evidence_rng.gauss(12.0, 4.0)))
        if config.scenario == "orders_queue_backlog" and index >= config.window_count - 4:
            orders_queue_depth += 900 + 100 * (index - (config.window_count - 4))
            orders_errors += _poisson(evidence_rng, 20.0)
        events.append(GeneratedEvent(
            simulated_at=_iso(window_start + timedelta(minutes=6)), source="product_logs",
            event_type="service_log_summary",
            payload={"service": "orders-api", "requests": orders_requests, "errors": orders_errors,
                     "queue_depth": orders_queue_depth,
                     "oldest_job_seconds": max(1, int(orders_queue_depth * 1.5)),
                     "level": "warning" if orders_queue_depth > 100 else "info",
                     "message": "Order worker queue depth elevated; jobs are waiting for processing."
                     if orders_queue_depth > 100 else "Order worker queue within normal range."},
        ))
        delivery_requests = 400 + _poisson(evidence_rng, 35.0)
        delivery_errors = _poisson(evidence_rng, 2.0)
        carrier_latency_ms = max(80, int(evidence_rng.gauss(240, 35)))
        if config.scenario == "delivery_carrier_degradation" and index >= config.window_count - 4:
            delivery_errors += _poisson(evidence_rng, 95.0)
            carrier_latency_ms += 1800 + 300 * (index - (config.window_count - 4))
        events.append(GeneratedEvent(
            simulated_at=_iso(window_start + timedelta(minutes=8)), source="product_logs",
            event_type="service_log_summary",
            payload={"service": "delivery-api", "dependency": "northstar-carrier-api",
                     "requests": delivery_requests, "errors": delivery_errors,
                     "p95_latency_ms": carrier_latency_ms,
                     "rate_limited": delivery_errors if carrier_latency_ms > 1000 else 0,
                     "level": "error" if delivery_errors > 20 else "info",
                     "message": "Carrier API timeouts and rate limits observed."
                     if delivery_errors > 20 else "Carrier API responses within normal range."},
        ))

        means = dict(zip(CATEGORIES, config.baseline_category_means, strict=True))
        incident_addition = 0.0
        if scenario_category and index >= config.window_count - 4:
            incident_addition = (failed_attempts * config.contact_probability_per_failure
                                 if scenario_category == "payments" else 22.0)
        if config.arrival_model == "negative_binomial":
            category_counts = {
                category: _negative_binomial(
                    rng,
                    means[category] + (incident_addition if category == scenario_category else 0.0),
                    config.negative_binomial_shape,
                )
                for category in CATEGORIES
            }
        else:
            category_counts = {
                category: _poisson(
                    rng,
                    means[category] + (incident_addition if category == scenario_category else 0.0),
                )
                for category in CATEGORIES
            }
        ticket_count = sum(category_counts.values())
        incident_ticket_share = min(
            1.0,
            incident_addition / max(category_counts.get(scenario_category or "", 0), 1),
        )
        ticket_number = 0
        for category, category_ticket_count in category_counts.items():
            for _ in range(category_ticket_count if include_ticket_events else 0):
                is_incident_ticket = category == scenario_category and ticket_rng.random() < incident_ticket_share
                offset = ticket_rng.randrange(WINDOW_MINUTES)
                ticket_time = window_start + timedelta(minutes=offset)
                if category == "payments":
                    if is_incident_ticket:
                        reason, title, body = message_rng.choice((
                            ("authorization_failure", "Payment did not go through", "I tried to pay at checkout, but the payment did not go through. The authorization request timed out."),
                            ("authorization_failure", "Checkout showed an authorization error", "Checkout returned an authorization error when I submitted payment, so I could not complete the purchase."),
                            ("payment_timeout", "Payment is still processing", "I have been waiting for the payment confirmation, but checkout timed out before showing whether it worked."),
                            ("charge_attempt_failed", "Charged attempt failed", "My payment attempt failed after waiting for a response. I still cannot tell whether the order was placed."),
                            ("checkout_unclear", "Checkout did not finish", "The payment page kept spinning and then returned me to checkout. I am not sure whether my order went through."),
                        ))
                    else:
                        reason, title, body = message_rng.choice((
                            ("refund_request", "Question about a refund", "Could you check when my recent refund will arrive?"),
                            ("card_declined", "Card was declined", "My card was declined at checkout. Can you help me understand why?"),
                            ("billing_question", "Question about a charge", "I have a question about a charge shown on my account."),
                        ))
                elif category == "orders" and is_incident_ticket:
                    reason, title, body = message_rng.choice((
                        ("order_processing_delay", "Order is still processing", "My order was accepted, but it has stayed in processing much longer than usual."),
                        ("order_status_stalled", "Order status has not moved", "The order page has shown the same processing status for hours. Can you check it?"),
                        ("checkout_order_unclear", "Not sure if my order completed", "Checkout finished, but I never received a clear confirmation that the order entered processing."),
                        ("order_processing_delay", "Order confirmation is delayed", "I submitted the order and payment, but the order confirmation is taking a long time."),
                    ))
                elif category == "delivery" and is_incident_ticket:
                    reason, title, body = message_rng.choice((
                        ("carrier_tracking_timeout", "Tracking is not updating", "The carrier tracking page times out and my package has no recent scan."),
                        ("delivery_status_stalled", "Delivery status is stuck", "My package status has not changed, and the carrier page is unavailable."),
                        ("carrier_eta_missing", "Delivery estimate disappeared", "The delivery estimate disappeared after the carrier tracking service stopped responding."),
                        ("carrier_tracking_timeout", "Carrier page will not load", "I cannot get a response from the carrier tracking link to check my delivery."),
                    ))
                else:
                    reason, title, body = message_rng.choice({
                        "orders": (
                            ("order_status", "Question about order status", "I placed an order but its status has not changed. Can you confirm whether it is being processed?"),
                            ("order_status", "Order status has not updated", "The confirmation arrived, but the order page still says processing. Please check its status."),
                            ("order_status", "Checking on my order", "I cannot see a recent update for my order and would like to know when it will ship."),
                        ),
                        "account": (
                            ("account_update", "Unable to update account details", "I cannot save the changes to my account details. The page returns me to the same form."),
                            ("account_update", "Account change will not save", "I updated my profile information, but it reverts when I return to the account page."),
                            ("account_update", "Problem changing account details", "The account form reports a problem when I try to save my updated details."),
                        ),
                        "delivery": (
                            ("delivery_estimate", "Delivery estimate unclear", "The delivery estimate keeps changing and I do not know when the package will arrive."),
                            ("delivery_estimate", "Package delivery date changed", "My package date moved again and the tracking page does not explain the delay."),
                            ("delivery_estimate", "Question about delivery timing", "Could you confirm the delivery date? The estimate on my order is unclear."),
                        ),
                    }[category])
                events.append(GeneratedEvent(
                    simulated_at=_iso(ticket_time), source="support_tickets", event_type="ticket_created",
                    payload={"ticket_key": f"SUP-{index:03d}-{ticket_number:03d}", "window_index": index, "title": title,
                             "body": body, "reason": reason, "category": category,
                             "channel": ticket_rng.choice(("web", "email", "chat")),
                             "customer_tier": ticket_rng.choice(("standard", "standard", "plus"))},
                ))
                ticket_number += 1

        events.sort(key=lambda event: event.simulated_at)
        yield GeneratedWindow(index=index, start=_iso(window_start), end=_iso(window_end),
                              ticket_count=ticket_count, category_counts=category_counts, events=events)
