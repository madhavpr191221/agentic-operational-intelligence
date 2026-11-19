"""Seeded stochastic generator for a fictional product and support system."""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from agentic_ops.domain import SimulationConfig

WINDOW_MINUTES = 30
SIMULATION_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def generate_run(config: SimulationConfig) -> Iterator[GeneratedWindow]:
    """Generate 72 simulated hours in 30-minute windows.

    The first day is a baseline. In the payment scenario, an issue is opened at
    simulated hour 24 (48 hours before the run ends), remains unresolved, and
    payment failures worsen near the end. Ticket volume is sampled from the
    configured count model at a mean linked to generated failures.
    """
    if config.window_count < 4:
        raise ValueError("window_count must be at least 4")
    if config.calibration_windows < 1 or config.calibration_windows >= config.window_count:
        raise ValueError("calibration_windows must be between 1 and window_count - 1")
    if not 0.0 <= config.contact_probability_per_failure <= 1.0:
        raise ValueError("contact_probability_per_failure must be between 0 and 1")

    rng = random.Random(config.seed)
    start = SIMULATION_START
    issue_window = min(config.calibration_windows, config.window_count // 3)
    resolved_window = min(issue_window + 12, config.window_count - 1)
    issue_key = "PAY-204" if config.scenario in ("payment_issue", "resolved_issue") else "CAT-118"

    for index in range(config.window_count):
        window_start = start + timedelta(minutes=WINDOW_MINUTES * index)
        window_end = window_start + timedelta(minutes=WINDOW_MINUTES)
        events: list[GeneratedEvent] = []

        if config.scenario in ("payment_issue", "resolved_issue") and index == issue_window:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start), source="issue_tracker", event_type="issue_created",
                payload={"issue_key": issue_key, "title": "Payment authorization timeouts", "status": "open",
                         "service": "checkout-payments", "description": "Intermittent authorization failures reported."},
            ))
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
        if config.scenario == "resolved_issue" and index == resolved_window:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start), source="issue_tracker", event_type="issue_resolved",
                payload={"issue_key": issue_key, "title": "Payment authorization timeouts", "status": "resolved",
                         "service": "checkout-payments", "resolution": "Retry configuration rolled back."},
            ))

        attempts = _poisson(rng, config.payment_attempts_per_window)
        failure_probability = 0.012
        if config.scenario == "payment_issue" and index >= issue_window:
            failure_probability = 0.025
            if index >= config.window_count - 4:
                failure_probability = 0.72
        failed_attempts = _binomial(rng, attempts, failure_probability)

        if config.scenario == "unrelated_issue" and index >= config.window_count - 4:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(minutes=4)), source="product_logs", event_type="service_log_summary",
                payload={"service": "catalog-search", "requests": attempts, "errors": max(1, _poisson(rng, 8.0)),
                         "level": "warning", "message": "Search suggestion latency elevated."},
            ))
        else:
            events.append(GeneratedEvent(
                simulated_at=_iso(window_start + timedelta(minutes=4)), source="product_logs", event_type="service_log_summary",
                payload={"service": "checkout-payments", "requests": attempts, "errors": failed_attempts,
                         "failure_probability": failure_probability, "level": "error" if failed_attempts else "info",
                         "message": "Payment authorization timeout observed." if failed_attempts else "Payment requests healthy."},
            ))

        expected_tickets = config.baseline_tickets_per_window + failed_attempts * config.contact_probability_per_failure
        if config.arrival_model == "negative_binomial":
            ticket_count = _negative_binomial(rng, expected_tickets, config.negative_binomial_shape)
        else:
            ticket_count = _poisson(rng, expected_tickets)

        payment_fraction = 0.0 if expected_tickets <= 0 else min(
            0.95, (failed_attempts * config.contact_probability_per_failure) / expected_tickets
        )
        for ticket_number in range(ticket_count):
            is_payment = rng.random() < payment_fraction
            offset = rng.randrange(WINDOW_MINUTES)
            ticket_time = window_start + timedelta(minutes=offset)
            if is_payment:
                title = rng.choice(("Payment did not go through", "Checkout showed an authorization error", "Charged attempt failed"))
                category = "payments"
            else:
                title = rng.choice(("Question about order status", "Unable to update account details", "Delivery estimate unclear"))
                category = rng.choice(("orders", "account", "delivery"))
            events.append(GeneratedEvent(
                simulated_at=_iso(ticket_time), source="support_tickets", event_type="ticket_created",
                payload={"ticket_key": f"SUP-{index:03d}-{ticket_number:03d}", "title": title,
                         "category": category, "channel": rng.choice(("web", "email", "chat")),
                         "customer_tier": rng.choice(("standard", "standard", "plus"))},
            ))

        events.sort(key=lambda event: event.simulated_at)
        yield GeneratedWindow(index=index, start=_iso(window_start), end=_iso(window_end),
                              ticket_count=ticket_count, events=events)
