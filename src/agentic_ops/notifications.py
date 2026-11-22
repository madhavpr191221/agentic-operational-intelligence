"""Durable incident attention, email delivery, acknowledgement, and escalation."""
from __future__ import annotations

import json
import logging
import os
import smtplib
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from dotenv import dotenv_values

from agentic_ops.store import EventStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
IMMEDIATE = "immediate"
REVIEW_SOON = "review_soon"
MONITOR = "monitor"
_LEVEL_RANK = {MONITOR: 0, REVIEW_SOON: 1, IMMEDIATE: 2}
_SERVICE_BY_CATEGORY = {
    "payments": "checkout-payments",
    "orders": "orders-api",
    "account": "account-api",
    "delivery": "delivery-api",
}
_DISPLAY_SERVICE_BY_CATEGORY = {
    "payments": "Payments",
    "orders": "Orders",
    "account": "Account",
    "delivery": "Delivery",
}
logger = logging.getLogger(__name__)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"aoi:{kind}:{key}"))


def _environment(name: str, values: dict[str, Any], default: str = "") -> str:
    return os.getenv(name, str(values.get(name) or default)).strip()


@dataclass(frozen=True)
class NotificationSettings:
    email_mode: str = "simulated"
    primary_email: str = "primary@example.test"
    backup_email: str = "backup@example.test"
    app_url: str = "http://127.0.0.1:5173"
    operator_name: str = "Local operator"
    acknowledgement_timeout_seconds: int = 600
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    trust_service_logs: bool = True
    payment_error_rate: float = 0.10
    general_error_rate: float = 0.10
    orders_queue_depth: int = 100
    delivery_p95_ms: int = 1000

    def __post_init__(self) -> None:
        if self.email_mode not in {"simulated", "smtp"}:
            raise ValueError("AOI_EMAIL_MODE must be 'simulated' or 'smtp'.")
        if self.acknowledgement_timeout_seconds < 1:
            raise ValueError("Acknowledgement timeout must be at least one second.")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535.")
        for name, value in (("payment error rate", self.payment_error_rate),
                            ("service error rate", self.general_error_rate)):
            if not 0 <= value <= 1:
                raise ValueError(f"Critical {name} must be between zero and one.")
        if self.orders_queue_depth < 0 or self.delivery_p95_ms < 1:
            raise ValueError("Queue-depth and latency cutoffs must be nonnegative and positive, respectively.")

    @classmethod
    def from_environment(cls) -> "NotificationSettings":
        values = dict(dotenv_values(REPOSITORY_ROOT / ".env"))
        mode = _environment("AOI_EMAIL_MODE", values, "simulated").casefold()
        return cls(
            email_mode=mode,
            primary_email=_environment("AOI_PRIMARY_EMAIL", values, "primary@example.test" if mode == "simulated" else ""),
            backup_email=_environment("AOI_BACKUP_EMAIL", values, "backup@example.test" if mode == "simulated" else ""),
            app_url=_environment("AOI_APP_URL", values, "http://127.0.0.1:5173").rstrip("/"),
            operator_name=_environment("AOI_OPERATOR_DISPLAY_NAME", values, "Local operator"),
            acknowledgement_timeout_seconds=int(_environment("AOI_ACK_TIMEOUT_SECONDS", values, "600")),
            smtp_host=_environment("AOI_SMTP_HOST", values),
            smtp_port=int(_environment("AOI_SMTP_PORT", values, "587")),
            smtp_username=_environment("AOI_SMTP_USERNAME", values),
            smtp_password=_environment("AOI_SMTP_PASSWORD", values),
            smtp_from=_environment("AOI_SMTP_FROM", values),
            smtp_starttls=_environment("AOI_SMTP_STARTTLS", values, "true").casefold() not in {"0", "false", "no"},
            trust_service_logs=_environment("AOI_TRUST_SERVICE_LOGS", values, "true").casefold() not in {"0", "false", "no"},
            payment_error_rate=float(_environment("AOI_CRITICAL_PAYMENT_ERROR_RATE", values, "0.10")),
            general_error_rate=float(_environment("AOI_CRITICAL_SERVICE_ERROR_RATE", values, "0.10")),
            orders_queue_depth=int(_environment("AOI_CRITICAL_ORDERS_QUEUE_DEPTH", values, "100")),
            delivery_p95_ms=int(_environment("AOI_CRITICAL_DELIVERY_P95_MS", values, "1000")),
        )


@dataclass(frozen=True)
class DeliveryResult:
    mode: str
    status: str = "dispatched"
    detail: str = ""


class DeliveryFailure(Exception):
    def __init__(self, message: str, *, safe_to_retry: bool, permanent: bool = False):
        super().__init__(message)
        self.safe_to_retry = safe_to_retry
        self.permanent = permanent


class EmailAdapter(Protocol):
    mode: str

    def send(self, recipient: str, subject: str, body: str) -> DeliveryResult: ...


class SimulatedEmailAdapter:
    mode = "simulated"

    def send(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        # Deliberately performs no network operation.
        return DeliveryResult("simulated", detail=f"SIMULATED EMAIL to {recipient}")


class SmtpEmailAdapter:
    mode = "smtp"

    def __init__(self, settings: NotificationSettings, timeout: float = 10.0):
        self.settings = settings
        self.timeout = timeout

    def send(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        settings = self.settings
        if not (settings.smtp_host and settings.smtp_from and recipient):
            raise DeliveryFailure("SMTP host, sender, and recipient must be configured.",
                                  safe_to_retry=False, permanent=True)
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        try:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=self.timeout)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryFailure(str(exc), safe_to_retry=True) from exc
        try:
            try:
                server.ehlo()
                if settings.smtp_starttls:
                    server.starttls()
                    server.ehlo()
                if settings.smtp_username:
                    server.login(settings.smtp_username, settings.smtp_password)
            except (OSError, smtplib.SMTPException) as exc:
                raise DeliveryFailure(str(exc), safe_to_retry=True) from exc
            try:
                refused = server.send_message(message)
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
                raise DeliveryFailure(str(exc), safe_to_retry=False, permanent=True) from exc
            except smtplib.SMTPDataError as exc:
                # A server rejection after DATA is an explicit non-acceptance.
                raise DeliveryFailure(str(exc), safe_to_retry=400 <= exc.smtp_code < 500,
                                      permanent=exc.smtp_code >= 500) from exc
            except Exception as exc:
                # A disconnect during/after DATA has an unknowable outcome.
                raise DeliveryFailure(str(exc), safe_to_retry=False) from exc
            if refused:
                raise DeliveryFailure(f"SMTP refused recipients: {sorted(refused)}",
                                      safe_to_retry=False, permanent=True)
            return DeliveryResult("smtp", detail="SMTP server accepted the message; delivery is unverified.")
        finally:
            try:
                server.close()
            except Exception:
                pass


class NotificationCoordinator:
    """Single-process dispatcher with durable SQLite state and independent polling."""

    def __init__(self, store: EventStore, settings: NotificationSettings | None = None,
                 email_adapter: EmailAdapter | None = None, clock: Any = None,
                 poll_seconds: float = 0.5, lease_seconds: int = 120):
        self.store = store
        self.settings = settings or NotificationSettings.from_environment()
        self.adapter = email_adapter or self._build_adapter(self.settings)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.poll_seconds = poll_seconds
        self.lease_seconds = max(3, lease_seconds)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="notification-dispatcher")
        self._initialize_schema()

    @staticmethod
    def _build_adapter(settings: NotificationSettings) -> EmailAdapter:
        if settings.email_mode == "simulated":
            return SimulatedEmailAdapter()
        if settings.email_mode == "smtp":
            return SmtpEmailAdapter(settings)
        raise ValueError("AOI_EMAIL_MODE must be 'simulated' or 'smtp'.")

    def _initialize_schema(self) -> None:
        with self.store._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS incident_attention (
                    incident_id TEXT PRIMARY KEY REFERENCES incidents(incident_id),
                    level TEXT NOT NULL, rationale TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL, evidence_cutoff TEXT,
                    evaluated_at TEXT NOT NULL, input_token TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incident_notifications (
                    notification_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL UNIQUE REFERENCES incidents(incident_id),
                    level TEXT NOT NULL, rationale TEXT NOT NULL,
                    created_at TEXT NOT NULL, acknowledgement_deadline TEXT,
                    acknowledged_at TEXT, acknowledged_by TEXT,
                    escalation_status TEXT NOT NULL DEFAULT 'none'
                );
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    outbox_id TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL REFERENCES incident_notifications(notification_id),
                    recipient_role TEXT NOT NULL, recipient TEXT NOT NULL,
                    subject TEXT NOT NULL, body TEXT NOT NULL,
                    status TEXT NOT NULL, delivery_mode TEXT NOT NULL DEFAULT 'simulated',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL, lease_until TEXT, last_error TEXT,
                    UNIQUE(notification_id, recipient_role)
                );
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_ready
                    ON notification_outbox(status, available_at);
                CREATE TABLE IF NOT EXISTS notification_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    outbox_id TEXT NOT NULL REFERENCES notification_outbox(outbox_id),
                    attempt_number INTEGER NOT NULL, mode TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT,
                    outcome TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_events (
                    event_id TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL REFERENCES incident_notifications(notification_id),
                    event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    actor TEXT, detail_json TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(notification_outbox)")}
            if "delivery_mode" not in columns:
                db.execute("ALTER TABLE notification_outbox ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'simulated'")
            if "draft_json" not in columns:
                db.execute("ALTER TABLE notification_outbox ADD COLUMN draft_json TEXT NOT NULL DEFAULT '{}'")

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception:
                # Keep monitoring and allow a transient SQLite/provider issue
                # to recover on the next polling pass. The failure is logged
                # through the outbox when it belongs to a delivery attempt.
                logger.exception("Notification dispatcher cycle failed")
            self._stop.wait(self.poll_seconds)

    def process_once(self, now: datetime | None = None) -> bool:
        now = now or self.clock()
        self.evaluate_incidents(now)
        worked = self.dispatch_one(now)
        escalated = self.schedule_one_escalation(now)
        return worked or escalated

    def evaluate_incidents(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        with self.store._connection() as db:
            incidents = db.execute("SELECT * FROM incidents ORDER BY opened_at,incident_id").fetchall()
        for row in incidents:
            assessment = self._assess(dict(row))
            if assessment is not None:
                self._save_assessment(dict(row), assessment, now)

    def _assess(self, incident: dict[str, Any]) -> dict[str, Any] | None:
        run_id = incident["stream_id"]
        category = incident["category"]
        episode = json.loads(incident["episode_json"])
        latest_alert_window = int(episode.get("latest_window", -1))
        with self.store._connection() as db:
            observation = db.execute(
                "SELECT sequence,payload_json FROM events WHERE run_id=? AND source='detector' "
                "AND event_type='window_observation' AND json_extract(payload_json,'$.window_index')=? "
                "ORDER BY sequence DESC LIMIT 1", (run_id, latest_alert_window),
            ).fetchone()
            report = db.execute("SELECT job_id,cutoff,sequence_bound,report_json FROM incident_jobs "
                                "WHERE incident_id=? AND status='completed' AND report_json IS NOT NULL "
                                "ORDER BY completed_at DESC,queue_order DESC LIMIT 1",
                                (incident["incident_id"],)).fetchone()
        if observation is None:
            return None
        obs = json.loads(observation["payload_json"])
        cutoff = obs["window_end"]
        max_sequence = int(observation["sequence"])
        start = (_parse_time(cutoff) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        records = self.store.search_events(run_id, ("support_tickets", "product_logs"), limit=1000,
                                           start_at=start, end_at=cutoff,
                                           max_sequence=max_sequence, as_of=cutoff)
        tickets = [event for event in records if event["source"] == "support_tickets"
                   and event["event_type"] == "ticket_created"
                   and event["payload"].get("category") == category]
        service_name = _SERVICE_BY_CATEGORY.get(category)
        metrics = [event for event in records if event["source"] == "product_logs"
                   and event["event_type"] == "service_log_summary"
                   and event["payload"].get("service") == service_name]
        critical = [event for event in metrics if self._critical_service_signal(category, event["payload"])]
        level, rationale, evidence = MONITOR, "No fresh evidence currently meets the Review soon or Immediate policy.", []
        if critical and self.settings.trust_service_logs:
            event = critical[-1]
            level = IMMEDIATE
            rationale = (f"Fresh trusted {service_name} telemetry crossed its configured critical condition; "
                         "please verify the possible customer impact urgently.")
            evidence = [event["evidence_id"]]
        elif tickets and critical:
            level = IMMEDIATE
            rationale = ("Fresh customer ticket records and independently collected service telemetry "
                         "support a potentially serious, ongoing issue; urgent verification is recommended.")
            evidence = [tickets[-1]["evidence_id"], critical[-1]["evidence_id"]]

        report_cutoff = None
        report_revision = "none"
        if report is not None:
            report_cutoff = report["cutoff"]
            report_revision = report["job_id"]
            report_payload = json.loads(report["report_json"])
            cited: set[str] = set()
            for hypothesis in report_payload.get("hypotheses", []):
                cited.update(hypothesis.get("supporting_evidence", []))
            cited_records = self.store.get_events_by_evidence_ids(
                run_id, list(cited), max_sequence=int(report["sequence_bound"]), as_of=report_cutoff)
            relevant = []
            for event in cited_records:
                payload = event["payload"]
                if (event["source"] == "support_tickets"
                        and payload.get("category") == category):
                    relevant.append(event)
                elif (event["source"] in {"product_logs", "issue_tracker", "change_log"}
                      and (payload.get("service") == service_name
                           or payload.get("category") == category)):
                    relevant.append(event)
            if level != IMMEDIATE and report_payload.get("status") == "completed" and relevant:
                level = REVIEW_SOON
                rationale = ("The investigation found a category-relevant operational record, but the "
                             "Immediate evidence bar was not met. Review the finding soon.")
                evidence = [event["evidence_id"] for event in relevant[:4]]
            # Immediate is assessed only from the latest alert window's raw
            # evidence above. A report summary or its older citations cannot
            # turn stale evidence into an urgent notification.
        token = f"{cutoff}:{report_revision}:{max_sequence}"
        return {"level": level, "rationale": rationale, "evidence_ids": evidence,
                "cutoff": report_cutoff if report_cutoff and level == REVIEW_SOON else cutoff,
                "input_token": token}

    def _critical_service_signal(self, category: str, payload: dict[str, Any]) -> bool:
        requests = int(payload.get("requests") or 0)
        errors = int(payload.get("errors") or 0)
        error_rate = errors / requests if requests > 0 else 0.0
        if category == "payments":
            return error_rate >= self.settings.payment_error_rate
        if category == "orders":
            return (int(payload.get("queue_depth") or 0) > self.settings.orders_queue_depth
                    or error_rate >= self.settings.general_error_rate)
        if category == "delivery":
            return (int(payload.get("p95_latency_ms") or 0) >= self.settings.delivery_p95_ms
                    or error_rate >= self.settings.general_error_rate)
        if category == "account":
            return error_rate >= self.settings.general_error_rate
        return False

    def _save_assessment(self, incident: dict[str, Any], assessment: dict[str, Any], now: datetime) -> None:
        incident_id = incident["incident_id"]
        notification_id = _stable_id("notification", incident_id)
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM incident_attention WHERE incident_id=?", (incident_id,)).fetchone()
            if old and old["input_token"] == assessment["input_token"]:
                return
            level = assessment["level"]
            rationale = assessment["rationale"]
            evidence_ids = assessment["evidence_ids"]
            if old and _LEVEL_RANK[old["level"]] > _LEVEL_RANK[level]:
                # Keep the highest attention level reached. A quiet window or
                # temporarily healthy metric is not proof that the incident
                # has been resolved; an authorized human closes it in Stage 4.
                level, rationale = old["level"], old["rationale"]
                evidence_ids = json.loads(old["evidence_ids_json"])
            db.execute("INSERT INTO incident_attention VALUES (?,?,?,?,?,?,?) "
                       "ON CONFLICT(incident_id) DO UPDATE SET level=excluded.level,rationale=excluded.rationale,"
                       "evidence_ids_json=excluded.evidence_ids_json,evidence_cutoff=excluded.evidence_cutoff,"
                       "evaluated_at=excluded.evaluated_at,input_token=excluded.input_token",
                       (incident_id, level, rationale, json.dumps(evidence_ids), assessment["cutoff"],
                        now.isoformat(), assessment["input_token"]))
            if level in {REVIEW_SOON, IMMEDIATE}:
                existing = db.execute("SELECT * FROM incident_notifications WHERE incident_id=?", (incident_id,)).fetchone()
                created = existing is None
                if existing is None:
                    ack_deadline = (now + timedelta(seconds=self.settings.acknowledgement_timeout_seconds)).isoformat() if level == IMMEDIATE else None
                    db.execute("INSERT INTO incident_notifications (notification_id,incident_id,level,rationale,created_at,"
                               "acknowledgement_deadline,escalation_status) VALUES (?,?,?,?,?,?,?)",
                               (notification_id, incident_id, level, rationale, now.isoformat(), ack_deadline,
                                "waiting" if level == IMMEDIATE else "none"))
                    self._event(db, notification_id, "notification_created", now,
                                {"level": level, "rationale": rationale})
                else:
                    previous_level = existing["level"]
                    deadline = existing["acknowledgement_deadline"]
                    escalation = existing["escalation_status"]
                    if level == IMMEDIATE and previous_level != IMMEDIATE and deadline is None:
                        deadline = (now + timedelta(seconds=self.settings.acknowledgement_timeout_seconds)).isoformat()
                        escalation = "waiting"
                        self._event(db, notification_id, "notification_upgraded", now,
                                    {"from": previous_level, "to": IMMEDIATE})
                    db.execute("UPDATE incident_notifications SET level=?,rationale=?,acknowledgement_deadline=?,"
                               "escalation_status=? WHERE notification_id=?",
                               (level, rationale, deadline, escalation, notification_id))
                if level == IMMEDIATE:
                    self._queue_email(db, notification_id, "primary", self.settings.primary_email,
                                      incident, rationale, now)
            db.commit()

    def _queue_email(self, db: Any, notification_id: str, role: str, recipient: str,
                     incident: dict[str, Any], rationale: str, now: datetime) -> None:
        outbox_id = _stable_id("email", f"{notification_id}:{role}")
        category = str(incident["category"])
        display_category = _DISPLAY_SERVICE_BY_CATEGORY.get(category, category.replace("_", " ").title())
        episode = json.loads(incident.get("episode_json") or "{}")
        evidence_cutoff = str(incident["latest_window_end"])
        window_end = int(episode.get("latest_window", -1))
        latest_count = None
        if window_end >= 0:
            observation = db.execute(
                "SELECT payload_json FROM events WHERE run_id=? AND source='detector' "
                "AND event_type='window_observation' "
                "AND json_extract(payload_json,'$.window_index')=? ORDER BY sequence DESC LIMIT 1",
                (incident["stream_id"], window_end),
            ).fetchone()
            if observation:
                counts = json.loads(observation["payload_json"]).get("category_counts", {})
                latest_count = counts.get(category)

        investigation_status = "No investigation report is available yet."
        finding = None
        investigation = db.execute(
            "SELECT status,cutoff,report_json FROM incident_jobs WHERE incident_id=? "
            "ORDER BY queue_order DESC,created_at DESC LIMIT 1", (incident["incident_id"],),
        ).fetchone()
        if investigation:
            investigation_status = str(investigation["status"]).replace("_", " ").title()
            if investigation["status"] == "completed" and investigation["report_json"]:
                report = json.loads(investigation["report_json"])
                try:
                    within_cutoff = _parse_time(investigation["cutoff"]) <= _parse_time(evidence_cutoff)
                except (TypeError, ValueError):
                    within_cutoff = False
                if within_cutoff:
                    finding = str(report.get("summary") or "").strip() or None

        subject_prefix = "BACKUP ESCALATION" if role == "backup" else "IMMEDIATE"
        subject = f"[{subject_prefix}] {display_category} incident needs verification"
        incident_url = (f"{self.settings.app_url}/?run={quote(str(incident['stream_id']), safe='')}"
                        f"#incident-{quote(str(incident['incident_id']), safe='')}")
        alert_windows = episode.get("window_count")
        peak_count = episode.get("peak_ticket_count")
        requested_response = ("The primary notification was not acknowledged before its deadline. Please take ownership, "
                              "verify customer impact, and coordinate the technical response."
                              if role == "backup" else
                              "Please acknowledge in Signalroom, verify customer impact, and coordinate the technical response.")
        draft: dict[str, Any] = {
            "schema_version": 1,
            "recipient_role": role,
            "to": recipient,
            "subject": subject,
            "priority": "Immediate",
            "category": category,
            "service": _SERVICE_BY_CATEGORY.get(category),
            "episode_started_at": incident["opened_at"],
            "latest_alert_window_end": evidence_cutoff,
            "evidence_cutoff": evidence_cutoff,
            "alert_window_count": alert_windows,
            "latest_window_ticket_count": latest_count,
            "peak_window_ticket_count": peak_count,
            "rationale": rationale,
            "investigation_status": investigation_status,
            "investigation_finding": finding,
            "uncertainty": "The underlying cause and exact customer impact are not confirmed by this notification.",
            "requested_response": requested_response,
            "incident_id": incident["incident_id"],
            "incident_url": incident_url,
        }
        body_lines = [
            "Signalroom incident notification",
            "",
            f"Priority: {draft['priority']}",
            f"Category: {display_category}",
        ]
        if draft["service"]:
            body_lines.append(f"Service: {draft['service']}")
        body_lines.extend([
            f"Alert episode began: {draft['episode_started_at']}",
            f"Latest alert window ended: {draft['latest_alert_window_end']}",
        ])
        if alert_windows is not None:
            body_lines.append(f"Alert windows so far: {alert_windows}")
        if latest_count is not None:
            body_lines.append(f"Tickets in latest alert window: {latest_count}")
        if peak_count is not None:
            body_lines.append(f"Peak tickets in one alert window: {peak_count}")
        body_lines.extend([
            "",
            f"Why attention is needed: {rationale}",
            f"Investigation status: {investigation_status}",
        ])
        if finding:
            body_lines.append(f"Finding available at notification time: {finding}")
        body_lines.extend([
            f"Uncertainty: {draft['uncertainty']}",
            f"Requested response: {requested_response}",
            f"Evidence available through: {evidence_cutoff}",
            f"Open incident: {incident_url}",
        ])
        draft["body"] = "\n".join(body_lines)
        body = draft["body"]
        if not recipient:
            status = "blocked"
            error = "No recipient is configured. The in-app notification remains available."
        else:
            status, error = "queued", None
        db.execute("INSERT OR IGNORE INTO notification_outbox (outbox_id,notification_id,recipient_role,recipient,"
                   "subject,body,status,delivery_mode,attempt_count,available_at,last_error,draft_json) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (outbox_id, notification_id, role, recipient, subject, body, status, self.adapter.mode, 0,
                    now.isoformat(), error, json.dumps(draft, sort_keys=True)))

    def _event(self, db: Any, notification_id: str, event_type: str, now: datetime,
               detail: dict[str, Any], key: str | None = None, actor: str | None = None) -> None:
        event_key = key or f"{event_type}:{now.isoformat()}:{json.dumps(detail, sort_keys=True)}"
        event_id = _stable_id("notification-event", f"{notification_id}:{event_key}")
        db.execute("INSERT OR IGNORE INTO notification_events VALUES (?,?,?,?,?,?)",
                   (event_id, notification_id, event_type, now.isoformat(), actor,
                    json.dumps(detail, sort_keys=True)))

    def dispatch_one(self, now: datetime | None = None) -> bool:
        now = now or self.clock()
        now_text = now.isoformat()
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE notification_outbox SET status='queued',last_error=NULL,available_at=? "
                       "WHERE status='blocked' AND delivery_mode=? "
                       "AND last_error LIKE 'Outbox was created for %'",
                       (now_text, self.adapter.mode))
            expired = db.execute("SELECT * FROM notification_outbox WHERE status='sending' AND lease_until<=?",
                                 (now_text,)).fetchall()
            for row in expired:
                if row["delivery_mode"] == "simulated":
                    db.execute("UPDATE notification_outbox SET status='queued',lease_until=NULL,"
                               "available_at=?,last_error='Interrupted simulated send; safe to replay.' WHERE outbox_id=?",
                               (now_text, row["outbox_id"]))
                    outcome = "safe_retry"
                else:
                    db.execute("UPDATE notification_outbox SET status='unknown',lease_until=NULL,"
                               "last_error='SMTP outcome unknown after dispatcher lease expired.' WHERE outbox_id=?",
                               (row["outbox_id"],))
                    outcome = "unknown"
                db.execute("UPDATE notification_attempts SET finished_at=?,outcome=?,detail=? "
                           "WHERE outbox_id=? AND attempt_number=? AND finished_at IS NULL",
                           (now_text, outcome, "Dispatcher stopped during delivery.", row["outbox_id"], row["attempt_count"]))
                self._event(db, row["notification_id"], "delivery_outcome_unknown" if outcome == "unknown" else "simulated_delivery_requeued",
                            now, {"outbox_id": row["outbox_id"]}, key=f"lease-expired:{row['outbox_id']}:{row['attempt_count']}")
            row = db.execute("SELECT * FROM notification_outbox WHERE status='queued' AND available_at<=? "
                             "ORDER BY available_at,outbox_id LIMIT 1", (now_text,)).fetchone()
            if row is None:
                db.commit()
                return False
            if not row["recipient"]:
                db.execute("UPDATE notification_outbox SET status='blocked',last_error=? WHERE outbox_id=?",
                           ("No recipient is configured.", row["outbox_id"]))
                self._event(db, row["notification_id"], "delivery_blocked", now,
                            {"reason": "No recipient is configured."}, key=f"blocked:{row['outbox_id']}")
                db.commit()
                return True
            if row["delivery_mode"] != self.adapter.mode:
                db.execute("UPDATE notification_outbox SET status='blocked',last_error=? WHERE outbox_id=?",
                           (f"Outbox was created for {row['delivery_mode']} mode; current configuration is {self.adapter.mode}. "
                            "Review the item and restore the intended mode before retrying.", row["outbox_id"]))
                self._event(db, row["notification_id"], "delivery_blocked", now,
                            {"reason": "Delivery mode changed since this outbox item was created.",
                            "created_mode": row["delivery_mode"], "configured_mode": self.adapter.mode},
                            key=f"mode-mismatch:{row['outbox_id']}")
                if row["recipient_role"] == "backup":
                    db.execute("UPDATE incident_notifications SET escalation_status='delivery_blocked' "
                               "WHERE notification_id=? AND acknowledged_at IS NULL",
                               (row["notification_id"],))
                db.commit()
                return True
            attempt = row["attempt_count"] + 1
            db.execute("UPDATE notification_outbox SET status='sending',attempt_count=?,lease_until=?,last_error=NULL WHERE outbox_id=?",
                       (attempt, (now + timedelta(seconds=self.lease_seconds)).isoformat(), row["outbox_id"]))
            cursor = db.execute("INSERT INTO notification_attempts (outbox_id,attempt_number,mode,started_at,outcome,detail) "
                                "VALUES (?,?,?,?,?,?)",
                                (row["outbox_id"], attempt, self.adapter.mode, now_text, "started", ""))
            attempt_id = cursor.lastrowid
            claimed = dict(row)
            claimed["attempt_count"] = attempt
        try:
            result = self.adapter.send(claimed["recipient"], claimed["subject"], claimed["body"])
            outcome, detail = result.status, result.detail
            retry = False
            final_status = "dispatched"
        except DeliveryFailure as exc:
            detail = str(exc)
            retry = exc.safe_to_retry and not exc.permanent and claimed["attempt_count"] < 2
            outcome = "failed_safe_to_retry" if retry else ("failed" if exc.safe_to_retry or exc.permanent else "unknown")
            final_status = "queued" if retry else ("failed" if exc.safe_to_retry or exc.permanent else "unknown")
        except Exception as exc:
            detail = str(exc)
            outcome, final_status, retry = "unknown", "unknown", False
        finished = self.clock()
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE notification_attempts SET finished_at=?,outcome=?,detail=? WHERE attempt_id=?",
                       (finished.isoformat(), outcome, detail, attempt_id))
            delay = timedelta(seconds=5) if retry else timedelta()
            db.execute("UPDATE notification_outbox SET status=?,lease_until=NULL,available_at=?,last_error=? "
                       "WHERE outbox_id=? AND status='sending'",
                       (final_status, (finished + delay).isoformat(), detail, claimed["outbox_id"]))
            if final_status == "dispatched":
                event_type = "notification_dispatched"
                if result.mode == "simulated":
                    event_type = "notification_simulated"
                self._event(db, claimed["notification_id"], event_type, finished,
                            {"outbox_id": claimed["outbox_id"], "role": claimed["recipient_role"],
                             "mode": result.mode, "detail": detail}, key=f"dispatch:{claimed['outbox_id']}:{claimed['attempt_count']}")
                if claimed["recipient_role"] == "backup":
                    notification = db.execute("SELECT acknowledged_at,acknowledged_by FROM incident_notifications WHERE notification_id=?",
                                              (claimed["notification_id"],)).fetchone()
                    late_ack = bool(notification and notification["acknowledged_at"])
                    db.execute("UPDATE incident_notifications SET escalation_status=? WHERE notification_id=?",
                               ("superseded" if late_ack else "escalated", claimed["notification_id"]))
                    self._event(db, claimed["notification_id"], "notification_escalated", finished,
                                {"outbox_id": claimed["outbox_id"], "mode": result.mode}, key="escalated")
                    if late_ack:
                        self._event(db, claimed["notification_id"], "notification_escalation_superseded", finished,
                                    {"reason": "Primary acknowledgement arrived while backup delivery was in flight."},
                                    key="escalation-superseded", actor=notification["acknowledged_by"])
            elif final_status == "unknown":
                self._event(db, claimed["notification_id"], "delivery_outcome_unknown", finished,
                            {"outbox_id": claimed["outbox_id"], "detail": detail}, key=f"unknown:{claimed['outbox_id']}:{claimed['attempt_count']}")
            elif final_status == "failed":
                self._event(db, claimed["notification_id"], "notification_delivery_failed", finished,
                            {"outbox_id": claimed["outbox_id"], "detail": detail}, key=f"failed:{claimed['outbox_id']}:{claimed['attempt_count']}")
            db.commit()
        return True

    def schedule_one_escalation(self, now: datetime | None = None) -> bool:
        now = now or self.clock()
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT n.*,i.category,i.latest_window_end,i.incident_id,i.stream_id,i.opened_at,i.episode_json "
                             "FROM incident_notifications n "
                             "JOIN incidents i ON i.incident_id=n.incident_id "
                             "WHERE n.level=? AND n.acknowledged_at IS NULL AND n.escalation_status='waiting' "
                             "AND n.acknowledgement_deadline<=? ORDER BY n.acknowledgement_deadline LIMIT 1",
                             (IMMEDIATE, now.isoformat())).fetchone()
            if row is None:
                return False
            if not self.settings.backup_email:
                db.execute("UPDATE incident_notifications SET escalation_status='unavailable' WHERE notification_id=? AND acknowledged_at IS NULL",
                           (row["notification_id"],))
                self._event(db, row["notification_id"], "notification_escalation_unavailable", now,
                            {"reason": "No backup recipient is configured."}, key="escalation-unavailable")
                return True
            db.execute("UPDATE incident_notifications SET escalation_status='queued' WHERE notification_id=? "
                       "AND acknowledged_at IS NULL AND escalation_status='waiting'",
                       (row["notification_id"],))
            self._queue_email(db, row["notification_id"], "backup", self.settings.backup_email,
                              dict(row), row["rationale"], now)
            self._event(db, row["notification_id"], "notification_escalation_queued", now,
                        {"recipient_role": "backup"}, key="escalation-queued")
            return True

    def acknowledge(self, notification_id: str) -> dict[str, Any]:
        now = self.clock()
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM incident_notifications WHERE notification_id=?", (notification_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown notification")
            if row["level"] != IMMEDIATE:
                raise ValueError("Only Immediate notifications require acknowledgement.")
            if row["acknowledged_at"]:
                return dict(row)
            escalation = row["escalation_status"]
            if escalation in {"waiting", "queued", "delivery_blocked"}:
                escalation = "cancelled"
            elif escalation == "escalated":
                escalation = "superseded"
            db.execute("UPDATE incident_notifications SET acknowledged_at=?,acknowledged_by=?,escalation_status=? "
                       "WHERE notification_id=? AND acknowledged_at IS NULL",
                       (now.isoformat(), self.settings.operator_name, escalation, notification_id))
            db.execute("UPDATE notification_outbox SET status='cancelled',last_error='Acknowledged before dispatch.' "
                       "WHERE notification_id=? AND recipient_role='backup' AND status='queued'", (notification_id,))
            self._event(db, notification_id, "notification_acknowledged", now,
                        {"escalation_status": escalation}, key="acknowledged", actor=self.settings.operator_name)
            if escalation == "superseded":
                self._event(db, notification_id, "notification_escalation_superseded", now,
                            {"reason": "Primary acknowledgement arrived after backup escalation."},
                            key="escalation-superseded", actor=self.settings.operator_name)
            result = dict(db.execute("SELECT * FROM incident_notifications WHERE notification_id=?", (notification_id,)).fetchone())
            return result

    def list_assessments(self, stream_id: str | None = None) -> list[dict[str, Any]]:
        query = ("SELECT a.*,i.category,i.stream_id,i.status AS episode_status,i.operational_status,"
                 "n.notification_id,n.level AS notification_level,n.created_at AS notification_created_at,"
                 "n.acknowledgement_deadline,n.acknowledged_at,n.acknowledged_by,n.escalation_status "
                 "FROM incident_attention a JOIN incidents i ON i.incident_id=a.incident_id "
                 "LEFT JOIN incident_notifications n ON n.incident_id=a.incident_id")
        params: tuple[Any, ...] = ()
        if stream_id:
            query += " WHERE i.stream_id=?"
            params = (stream_id,)
        query += " ORDER BY CASE a.level WHEN 'immediate' THEN 0 WHEN 'review_soon' THEN 1 ELSE 2 END, i.opened_at DESC"
        with self.store._connection() as db:
            rows = db.execute(query, params).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["evidence_ids"] = json.loads(item.pop("evidence_ids_json"))
                if item["notification_id"]:
                    item["attempts"] = [dict(attempt) for attempt in db.execute(
                        "SELECT a.*,o.recipient_role,o.recipient,o.status AS delivery_status FROM notification_attempts a "
                        "JOIN notification_outbox o ON o.outbox_id=a.outbox_id WHERE o.notification_id=? "
                        "ORDER BY a.attempt_id", (item["notification_id"],)).fetchall()]
                    item["history"] = [dict(event) for event in db.execute(
                        "SELECT event_type,occurred_at,actor,detail_json FROM notification_events "
                        "WHERE notification_id=? ORDER BY occurred_at,event_id", (item["notification_id"],)).fetchall()]
                    for event in item["history"]:
                        event["detail"] = json.loads(event.pop("detail_json"))
                    item["outbox"] = [dict(outbox) for outbox in db.execute(
                        "SELECT recipient_role,recipient,subject,body,draft_json,status,delivery_mode,attempt_count,available_at,last_error FROM notification_outbox "
                        "WHERE notification_id=? ORDER BY recipient_role", (item["notification_id"],)).fetchall()]
                    for outbox in item["outbox"]:
                        saved_draft = json.loads(outbox.pop("draft_json") or "{}")
                        # Older outbox rows predate the structured draft and
                        # remain previewable from their durable message fields.
                        outbox["draft"] = saved_draft or {
                            "schema_version": 1,
                            "recipient_role": outbox["recipient_role"],
                            "to": outbox["recipient"],
                            "subject": outbox["subject"],
                            "body": outbox["body"],
                        }
                else:
                    item["attempts"], item["history"], item["outbox"] = [], [], []
                result.append(item)
        return result
