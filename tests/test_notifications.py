from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from agentic_ops.detection import CategoryDetectorSuite
from agentic_ops.detector_host import DetectorHost
from agentic_ops.domain import CATEGORIES
from agentic_ops.notifications import (
    IMMEDIATE,
    MONITOR,
    REVIEW_SOON,
    DeliveryResult,
    DeliveryFailure,
    NotificationCoordinator,
    NotificationSettings,
    SimulatedEmailAdapter,
)
from agentic_ops.store import EventStore


BASE = "2026-01-01T10:30:00Z"


class FixedClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class RecordingAdapter:
    mode = "simulated"

    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []

    def send(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        self.messages.append((recipient, subject, body))
        return DeliveryResult("simulated", detail=f"SIMULATED EMAIL to {recipient}")


@pytest.fixture
def notification_fixture(tmp_path):
    store = EventStore(tmp_path / "notifications.sqlite3")
    run_id = store.create_run("payment_issue", 4, "poisson", {})
    DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 48, CATEGORIES))
    incident_id = "episode-payments-1"
    with store._connection() as db:
        db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json,operational_status) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (incident_id, run_id, "payments", "active", BASE, BASE, "event-1",
                    json.dumps({"category": "payments", "start_window": 0, "latest_window": 1}), "open"))
    clock = FixedClock(datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc))
    adapter = RecordingAdapter()
    settings = NotificationSettings(
        email_mode="simulated", primary_email="primary@example.test", backup_email="backup@example.test",
        operator_name="Demo responder", acknowledgement_timeout_seconds=600,
        trust_service_logs=True,
    )
    coordinator = NotificationCoordinator(store, settings, adapter, clock=clock, poll_seconds=0.01)
    return store, run_id, incident_id, clock, adapter, coordinator


def add_observation(store: EventStore, run_id: str, *, tickets: int = 0,
                    requests: int = 100, errors: int = 0, service: str = "checkout-payments"):
    start = "2026-01-01T10:00:00Z"
    store.add_event(run_id, "2026-01-01T10:10:00Z", "support_tickets", "ticket_created",
                    {"category": "payments", "title": "Payment failed"})
    store.add_event(run_id, "2026-01-01T10:12:00Z", "product_logs", "service_log_summary",
                    {"service": service, "requests": requests, "errors": errors})
    store.add_event(run_id, BASE, "detector", "window_observation",
                    {"window_start": start, "window_end": BASE, "window_index": 1,
                     "category_counts": {"payments": tickets}})


def test_ticket_summary_or_ticket_events_alone_never_mean_immediate(notification_fixture):
    store, run_id, incident_id, clock, _, coordinator = notification_fixture
    add_observation(store, run_id, tickets=20, errors=0)
    coordinator.evaluate_incidents()
    item = coordinator.list_assessments(run_id)[0]
    assert item["level"] == MONITOR
    assert item["notification_id"] is None


def test_later_windows_cannot_change_the_episode_alert_snapshot(notification_fixture):
    store, run_id, _, _, _, coordinator = notification_fixture
    store.add_event(run_id, "2026-01-01T10:10:00Z", "support_tickets", "ticket_created",
                    {"category": "payments", "title": "Payment failure"})
    store.add_event(run_id, "2026-01-01T10:12:00Z", "product_logs", "service_log_summary",
                    {"service": "checkout-payments", "requests": 100, "errors": 0})
    store.add_event(run_id, BASE, "detector", "window_observation",
                    {"window_index": 1, "window_start": "2026-01-01T10:00:00Z", "window_end": BASE})
    store.add_event(run_id, "2026-01-01T10:40:00Z", "product_logs", "service_log_summary",
                    {"service": "checkout-payments", "requests": 100, "errors": 90})
    store.add_event(run_id, "2026-01-01T11:00:00Z", "detector", "window_observation",
                    {"window_index": 2, "window_start": BASE, "window_end": "2026-01-01T11:00:00Z"})
    coordinator.evaluate_incidents()
    assert coordinator.list_assessments(run_id)[0]["level"] == MONITOR


def test_immediate_requires_trusted_signal_or_independent_corrobation(notification_fixture):
    store, run_id, incident_id, clock, _, coordinator = notification_fixture
    add_observation(store, run_id, tickets=0, errors=20)
    coordinator.evaluate_incidents()
    assert coordinator.list_assessments(run_id)[0]["level"] == IMMEDIATE

    store.add_event(run_id, "2026-01-01T10:40:00Z", "support_tickets", "ticket_created",
                    {"category": "payments", "title": "Payment failed"})
    store.add_event(run_id, "2026-01-01T10:42:00Z", "product_logs", "service_log_summary",
                    {"service": "checkout-payments", "requests": 100, "errors": 20})
    store.add_event(run_id, "2026-01-01T11:00:00Z", "detector", "window_observation",
                    {"window_start": BASE, "window_end": "2026-01-01T11:00:00Z", "window_index": 2})
    with store._connection() as db:
        db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json,operational_status) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   ("episode-payments-2", run_id, "payments", "active", BASE, BASE, "event-2",
                    json.dumps({"category": "payments", "start_window": 1, "latest_window": 2}), "open"))
    coordinator.settings = NotificationSettings(trust_service_logs=False)
    coordinator.evaluate_incidents()
    item = next(item for item in coordinator.list_assessments(run_id) if item["incident_id"] == "episode-payments-2")
    assert item["level"] == IMMEDIATE
    assert len(item["evidence_ids"]) == 2


def test_same_source_evidence_and_untrusted_single_source_do_not_qualify(notification_fixture):
    store, run_id, _, _, _, coordinator = notification_fixture
    ticket = store.add_event(run_id, "2026-01-01T10:10:00Z", "support_tickets", "ticket_created",
                             {"category": "payments", "title": "Payment failed"})
    store.add_event(run_id, "2026-01-01T10:12:00Z", "product_logs", "service_log_summary",
                    {"service": "checkout-payments", "requests": 100, "errors": 0})
    store.add_event(run_id, BASE, "detector", "window_observation",
                    {"window_start": "2026-01-01T10:00:00Z", "window_end": BASE, "window_index": 1})
    with store._connection() as db:
        db.execute("INSERT INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,episode_json,status,created_at,report_json,error,queue_order,queued_at,completed_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("job-1", "episode-payments-1", run_id, "initial alert", BASE, 2,
                    json.dumps({"category": "payments"}), "completed", BASE,
                    json.dumps({"status": "completed", "hypotheses": [{"supporting_evidence": [ticket["evidence_id"]]}]}),
                    None, 1, BASE, BASE))
    coordinator.settings = NotificationSettings(trust_service_logs=False)
    coordinator.evaluate_incidents()
    assert coordinator.list_assessments(run_id)[0]["level"] == REVIEW_SOON


def test_report_evidence_for_an_unrelated_service_does_not_raise_attention(notification_fixture):
    store, run_id, incident_id, _, _, coordinator = notification_fixture
    store.add_event(run_id, "2026-01-01T10:10:00Z", "product_logs", "service_log_summary",
                    {"service": "checkout-payments", "requests": 100, "errors": 0})
    observation = store.add_event(run_id, BASE, "detector", "window_observation",
                                 {"window_start": "2026-01-01T10:00:00Z", "window_end": BASE,
                                  "window_index": 1})
    change = store.add_event(run_id, "2026-01-01T10:05:00Z", "change_log", "change_record",
                             {"service": "catalog-search", "summary": "Unrelated search change"})
    with store._connection() as db:
        db.execute("INSERT INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,episode_json,status,created_at,report_json,error,queue_order,queued_at,completed_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("job-unrelated", incident_id, run_id, "initial alert", BASE, change["sequence"],
                    json.dumps({"category": "payments"}), "completed", BASE,
                    json.dumps({"status": "completed", "hypotheses": [{"supporting_evidence": [change["evidence_id"]]}]}),
                    None, 1, BASE, BASE))
    coordinator.evaluate_incidents()
    assert coordinator.list_assessments(run_id)[0]["level"] == MONITOR


def test_simulated_email_dispatch_and_acknowledgement_are_durable(notification_fixture):
    store, run_id, _, clock, adapter, coordinator = notification_fixture
    add_observation(store, run_id, tickets=20, errors=20)
    coordinator.evaluate_incidents()
    coordinator.evaluate_incidents()  # duplicate poll/re-delivery must not create another email
    assert len(coordinator.list_assessments(run_id)[0]["outbox"]) == 1
    assert coordinator.dispatch_one()
    assert len(adapter.messages) == 1
    item = coordinator.list_assessments(run_id)[0]
    assert item["outbox"][0]["status"] == "dispatched"
    draft = item["outbox"][0]["draft"]
    assert draft["schema_version"] == 1
    assert draft["recipient_role"] == "primary"
    assert draft["to"] == "primary@example.test"
    assert draft["priority"] == "Immediate"
    assert draft["category"] == "payments"
    assert draft["latest_window_ticket_count"] == 20
    assert draft["evidence_cutoff"] == BASE
    assert draft["uncertainty"].endswith("not confirmed by this notification.")
    assert f"?run={run_id}#incident-{item['incident_id']}" in draft["incident_url"]
    assert draft["incident_url"] in draft["body"]
    assert "The underlying cause and exact customer impact are not confirmed" in draft["body"]
    assert "delivered" not in {event["event_type"] for event in item["history"]}
    notification_id = item["notification_id"]
    acknowledged = coordinator.acknowledge(notification_id)
    assert acknowledged["acknowledged_by"] == "Demo responder"
    assert acknowledged["escalation_status"] == "cancelled"


def test_one_backup_escalation_and_late_ack_supersedes_it(notification_fixture):
    store, run_id, _, clock, adapter, coordinator = notification_fixture
    add_observation(store, run_id, errors=20)
    coordinator.evaluate_incidents()
    assert coordinator.dispatch_one()
    item = coordinator.list_assessments(run_id)[0]
    deadline = datetime.fromisoformat(item["acknowledgement_deadline"])
    assert coordinator.schedule_one_escalation(deadline - timedelta(seconds=1)) is False
    assert coordinator.schedule_one_escalation(deadline) is True
    assert coordinator.dispatch_one(deadline)
    acknowledged = coordinator.acknowledge(item["notification_id"])
    assert acknowledged["escalation_status"] == "superseded"
    backup = next(row for row in coordinator.list_assessments(run_id)[0]["outbox"]
                  if row["recipient_role"] == "backup")
    assert backup["draft"]["subject"].startswith("[BACKUP ESCALATION]")
    assert "not acknowledged before its deadline" in backup["draft"]["body"]
    history = coordinator.list_assessments(run_id)[0]["history"]
    assert any(event["event_type"] == "notification_escalated" for event in history)
    assert any(event["event_type"] == "notification_escalation_superseded" for event in history)


def test_missing_backup_records_escalation_unavailable(notification_fixture):
    store, run_id, _, _, _, coordinator = notification_fixture
    add_observation(store, run_id, errors=20)
    coordinator.settings = NotificationSettings(backup_email="")
    coordinator.evaluate_incidents()
    item = coordinator.list_assessments(run_id)[0]
    assert coordinator.schedule_one_escalation(datetime.fromisoformat(item["acknowledgement_deadline"]))
    assert coordinator.list_assessments(run_id)[0]["escalation_status"] == "unavailable"


def test_acknowledgement_during_backup_send_is_recorded_as_superseded(notification_fixture):
    store, run_id, _, _, _, coordinator = notification_fixture
    add_observation(store, run_id, errors=20)
    coordinator.evaluate_incidents()
    coordinator.dispatch_one()  # primary delivery
    item = coordinator.list_assessments(run_id)[0]
    deadline = datetime.fromisoformat(item["acknowledgement_deadline"])
    coordinator.schedule_one_escalation(deadline)

    class AckDuringSend:
        mode = "simulated"

        def send(self, recipient, subject, body):
            coordinator.acknowledge(item["notification_id"])
            return DeliveryResult("simulated", detail=f"SIMULATED EMAIL to {recipient}")

    coordinator.adapter = AckDuringSend()
    assert coordinator.dispatch_one(deadline)
    final = coordinator.list_assessments(run_id)[0]
    assert final["escalation_status"] == "superseded"
    assert any(event["event_type"] == "notification_escalation_superseded" for event in final["history"])


def test_simulated_adapter_is_explicitly_local_only():
    result = SimulatedEmailAdapter().send("operator@example.test", "Incident", "Please verify")
    assert result.mode == "simulated"
    assert result.status == "dispatched"
    assert "SIMULATED EMAIL" in result.detail


def test_pending_delivery_survives_coordinator_recreation(notification_fixture):
    store, run_id, _, clock, adapter, coordinator = notification_fixture
    add_observation(store, run_id, errors=20)
    coordinator.evaluate_incidents()
    coordinator.stop()
    recovered = NotificationCoordinator(store, coordinator.settings, adapter, clock=clock)
    assert recovered.dispatch_one()
    assert len(adapter.messages) == 1
    assert recovered.list_assessments(run_id)[0]["outbox"][0]["status"] == "dispatched"


def test_simulated_outbox_never_switches_to_real_email_after_restart(notification_fixture):
    store, run_id, _, clock, _, coordinator = notification_fixture
    add_observation(store, run_id, errors=20)
    coordinator.evaluate_incidents()

    class SmtpTrap:
        mode = "smtp"

        def __init__(self):
            self.calls = 0

        def send(self, recipient, subject, body):
            self.calls += 1
            return DeliveryResult("smtp", detail="sent")

    trap = SmtpTrap()
    recovered = NotificationCoordinator(store, NotificationSettings(email_mode="smtp"), trap, clock=clock)
    assert recovered.dispatch_one()
    assert trap.calls == 0
    primary = next(row for row in recovered.list_assessments(run_id)[0]["outbox"]
                   if row["recipient_role"] == "primary")
    assert primary["delivery_mode"] == "simulated"
    assert primary["status"] == "blocked"
    recovered.adapter = SimulatedEmailAdapter()
    assert recovered.dispatch_one()
    assert recovered.list_assessments(run_id)[0]["outbox"][0]["status"] == "dispatched"


@pytest.mark.parametrize(
    ("safe", "expected_status", "attempts"),
    [(True, "queued", 1), (False, "unknown", 1)],
)
def test_delivery_failures_retry_only_when_outcome_is_known(notification_fixture, safe, expected_status, attempts):
    store, run_id, _, clock, _, coordinator = notification_fixture
    add_observation(store, run_id, errors=20)

    class FailingAdapter:
        mode = "smtp"

        def send(self, recipient, subject, body):
            raise DeliveryFailure("transient/uncertain", safe_to_retry=safe)

    coordinator.adapter = FailingAdapter()
    coordinator.evaluate_incidents()
    assert coordinator.dispatch_one()
    item = coordinator.list_assessments(run_id)[0]
    primary = next(row for row in item["outbox"] if row["recipient_role"] == "primary")
    assert primary["status"] == expected_status
    assert primary["attempt_count"] == attempts


def test_ticket_ingestion_is_not_blocked_by_slow_email_adapter(notification_fixture):
    store, run_id, _, _, _, coordinator = notification_fixture
    entered, release = threading.Event(), threading.Event()

    class SlowAdapter:
        mode = "smtp"

        def send(self, recipient, subject, body):
            entered.set()
            assert release.wait(timeout=3)
            return DeliveryResult("smtp", detail="accepted")

    coordinator.adapter = SlowAdapter()
    add_observation(store, run_id, errors=20)
    coordinator.evaluate_incidents()
    worker = threading.Thread(target=coordinator.dispatch_one)
    worker.start()
    assert entered.wait(timeout=2)
    event = store.add_event(run_id, "2026-01-01T10:20:00Z", "support_tickets", "ticket_created",
                            {"category": "payments", "title": "Still failing"})
    assert event["evidence_id"]
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
