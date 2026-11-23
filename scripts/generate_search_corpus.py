"""Generate a reproducible, fictional archive for local RAG search."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".data" / "corpus"
DEFAULT_SEED = 42
COUNTS = {"support_tickets": 4_000, "product_logs": 14_000,
          "issue_tracker": 1_000, "change_log": 1_000}
SERVICES = {"payments": "checkout-payments", "orders": "orders-api",
            "account": "account-api", "delivery": "delivery-api"}
CHANNELS = ("web", "email", "chat", "phone")
ARCHIVE_START = datetime(2025, 10, 1, tzinfo=timezone.utc)
ARCHIVE_DAYS = 92

TOPICS: dict[str, dict[str, Any]] = {
    "payment_authorization": {"category": "payments", "reason": "authorization_timeout",
        "phrases": ["authorization timed out", "the payment spinner never finished", "the bank approval took too long",
                     "checkout said it could not confirm my payment"],
        "queries": ["payment authorization timeout", "checkout bank approval taking too long",
                    "payment spinner never completes", "authorization request did not return"]},
    "payment_refund": {"category": "payments", "reason": "refund_status",
        "phrases": ["I am checking when the refund will arrive", "the refund is still pending", "please confirm my reversal"],
        "queries": ["payment refund pending", "where is the reversed payment", "refund status has not changed",
                    "check payment reimbursement timing"]},
    "payment_card_decline": {"category": "payments", "reason": "card_declined",
        "phrases": ["my card was declined", "the bank rejected the charge", "the card works elsewhere but not here"],
        "queries": ["card declined during checkout", "bank rejected charge", "payment card works elsewhere",
                    "issuer decline troubleshooting"]},
    "payment_duplicate": {"category": "payments", "reason": "duplicate_charge",
        "phrases": ["I see two pending charges", "the order page retried and charged me twice", "one purchase appears twice"],
        "queries": ["duplicate payment charge", "two pending transactions for one order",
                    "checkout retry charged twice", "same purchase appears twice"]},
    "payment_checkout": {"category": "payments", "reason": "checkout_frozen",
        "phrases": ["checkout froze after I entered the code", "the payment page stayed blank", "I could not finish placing the order"],
        "queries": ["checkout froze after verification", "blank payment page", "unable to finish placing an order",
                    "payment flow stopped responding"]},
    "orders_queue": {"category": "orders", "reason": "order_processing_delay",
        "phrases": ["my order is still processing", "the confirmation never arrived", "the order has not moved past received"],
        "queries": ["order worker processing backlog", "orders stuck after received", "order confirmation delayed",
                    "queue age and order processing delay"]},
    "orders_status": {"category": "orders", "reason": "order_status_stale",
        "phrases": ["the order status has not updated", "the page still says preparing", "I cannot see the latest order state"],
        "queries": ["stale order status", "order state did not update", "preparing status is unchanged",
                    "latest order status unavailable"]},
    "orders_cancel": {"category": "orders", "reason": "order_cancel_request",
        "phrases": ["please cancel before it ships", "I need to stop this order", "the cancel button did not respond"],
        "queries": ["cancel an order before shipment", "order cancellation request", "cancel button not responding",
                    "stop an order that has not shipped"]},
    "orders_inventory": {"category": "orders", "reason": "inventory_mismatch",
        "phrases": ["checkout accepted an item that is unavailable", "the item disappeared after I paid", "stock changed during checkout"],
        "queries": ["inventory mismatch in checkout", "item unavailable after payment", "stock changed during order",
                    "out of stock order acceptance"]},
    "delivery_tracking": {"category": "delivery", "reason": "tracking_stale",
        "phrases": ["tracking has not moved since yesterday", "the carrier page has no new scan", "my parcel still shows in transit"],
        "queries": ["stale carrier tracking", "shipment scan did not arrive", "parcel stuck in transit",
                    "latest delivery tracking event missing"]},
    "delivery_carrier_timeout": {"category": "delivery", "reason": "carrier_timeout",
        "phrases": ["the delivery estimate keeps failing to load", "carrier updates time out", "the tracking page says try again"],
        "queries": ["carrier API timeout", "delivery update request timed out", "tracking service unavailable",
                    "carrier response latency"]},
    "delivery_rate_limit": {"category": "delivery", "reason": "carrier_rate_limit",
        "phrases": ["the tracking service is temporarily unavailable", "the carrier says too many requests", "updates stopped after repeated attempts"],
        "queries": ["carrier rate limit exceeded", "delivery API throttling", "too many tracking requests",
                    "carrier request quota"]},
    "delivery_address": {"category": "delivery", "reason": "address_change",
        "phrases": ["I need to correct the delivery address", "the address on the parcel is incomplete", "can the courier use the new address"],
        "queries": ["change an address for delivery", "correct a parcel address", "courier has an incomplete address",
                    "delivery destination update"]},
    "account_login": {"category": "account", "reason": "login_lockout",
        "phrases": ["I am locked out after several codes", "the sign-in code never arrived", "the login page keeps asking me to retry"],
        "queries": ["account login lockout", "sign-in verification code missing", "too many login retries",
                    "account authentication problem"]},
    "account_identity_outage": {"category": "account", "reason": "sign_in_failure",
        "phrases": ["verification codes are delayed and sign-in fails", "identity checks are timing out for many account holders",
                    "customers cannot complete the sign-in verification step"],
        "queries": ["identity service sign-in failures and latency", "verification code delivery outage",
                    "account authentication errors after identity deployment", "identity-service error rate and login complaints"]},
    "account_identity_handoff": {"category": "account", "reason": "identity_service_degradation",
        "phrases": ["compare identity-service errors with sign-in ticket arrivals", "check verification delivery and authentication latency",
                    "confirm whether account-api or its identity dependency is failing"],
        "queries": ["identity-service incident handoff evidence", "account sign-in verification diagnostics",
                    "distinguish identity dependency failure from account-api failure", "verify sign-in recovery from error rate and ticket counts"]},
    "account_profile": {"category": "account", "reason": "profile_update",
        "phrases": ["my profile change did not save", "the account page reverted my details", "the new email is not showing"],
        "queries": ["account profile update not saved", "profile details reverted", "new email missing from account",
                    "customer profile change failed"]},
    "account_security": {"category": "account", "reason": "security_review",
        "phrases": ["the security check keeps looping", "I cannot complete the identity step", "the review page will not open"],
        "queries": ["account security review loop", "identity verification step fails", "security page will not open",
                    "account verification stuck"]},
    "catalog_search": {"category": "orders", "reason": "product_search_irrelevant",
        "phrases": ["search results do not match the product", "filters keep resetting", "the catalog shows unrelated items"],
        "queries": ["catalog search relevance regression", "product search results unrelated", "filters reset in catalog",
                    "search service rollout issue"]},
    "payment_gateway_latency": {"category": "payments", "reason": "authorization_timeout",
        "phrases": ["gateway response latency rose", "authorization requests are taking longer", "the payment dependency is slow"],
        "queries": ["payment gateway latency increase", "slow authorization dependency", "gateway response time elevated",
                    "payment dependency latency"]},
    "order_worker_queue": {"category": "orders", "reason": "order_processing_delay",
        "phrases": ["worker queue depth is rising", "oldest order job is waiting", "consumer lag increased on order events"],
        "queries": ["order worker queue depth rising", "oldest order job age", "order consumer lag increased",
                    "orders processing worker backlog"]},
    "carrier_dependency": {"category": "delivery", "reason": "carrier_timeout",
        "phrases": ["carrier dependency latency is high", "carrier requests are returning errors", "tracking fetches are delayed"],
        "queries": ["carrier dependency latency", "delivery provider error rate", "tracking fetch delayed by provider",
                    "external carrier API failure"]},
    "change_rollback": {"category": "payments", "reason": "authorization_timeout",
        "phrases": ["configuration rollback completed", "the previous retry policy was restored", "the change was reverted after review"],
        "queries": ["rollback payment retry configuration", "restore previous gateway retry policy",
                    "revert a configuration after review", "payment change rollback procedure"]},
    "runbook_verification": {"category": "payments", "reason": "authorization_timeout",
        "phrases": ["verify the authorization error rate", "compare two adjacent windows after the change", "check ticket arrivals before closing"],
        "queries": ["verify payment error rate after remediation", "check consecutive windows after change",
                    "payment tickets after rollback", "close incident after verification"]},
    "incident_handoff": {"category": "orders", "reason": "order_processing_delay",
        "phrases": ["record the first affected timestamp", "include the queue depth in handoff", "notify the owning service team"],
        "queries": ["handoff order queue incident", "record first affected time and queue depth",
                    "notify the order service owner", "incident handover details"]},
    "service_dependency": {"category": "delivery", "reason": "carrier_timeout",
        "phrases": ["check dependency health before restarting workers", "compare provider status with request latency", "capture the carrier response code"],
        "queries": ["check carrier dependency health", "provider status and request latency", "capture carrier response code",
                    "delivery dependency diagnosis"]},
    "historical_false_lead": {"category": "payments", "reason": "billing_question",
        "phrases": ["the nearby catalog update may be unrelated", "timing alone does not link the change to the error", "look for a matching service path before assigning cause"],
        "queries": ["temporally nearby but innocent catalog change", "timing alone does not prove cause",
                    "confirm affected service path before blame", "unrelated deployment distractor"]},
    "log_error_rate": {"category": "payments", "reason": "authorization_timeout",
        "phrases": ["error rate is computed from failed requests over total requests", "compare counts and rates across equal windows", "confirm that request volume is sufficient"],
        "queries": ["calculate service error rate from requests", "compare failure counts and rates",
                    "service requests denominator", "payment errors per request"]},
}


def _stable_id(seed: int, source: str, number: int) -> str:
    digest = hashlib.sha256(f"{seed}|{source}|{number}".encode()).hexdigest()[:16].upper()
    return f"ARC-{digest}"


def _passage_id(path_name: str, section_number: int, chunk_number: int = 1) -> str:
    digest = hashlib.sha256(f"{path_name}|{section_number}|{chunk_number}".encode("utf-8")).hexdigest()[:24]
    return f"DOC-{digest}"


def _timestamp(rng: random.Random) -> str:
    offset = rng.randrange(ARCHIVE_DAYS * 24 * 60)
    value = ARCHIVE_START + timedelta(minutes=offset, seconds=rng.randrange(60))
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _choose_topic(rng: random.Random, category: str) -> str:
    choices = [key for key, topic in TOPICS.items() if topic["category"] == category]
    return rng.choice(choices)


def _topic_for_service(rng: random.Random, service: str) -> str:
    category = next(category for category, value in SERVICES.items() if value == service)
    return _choose_topic(rng, category)


def generate(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    rng = random.Random(seed)
    records: list[dict[str, Any]] = []
    relevance: dict[str, list[str]] = defaultdict(list)
    source_counts: Counter[str] = Counter()

    for number in range(COUNTS["support_tickets"]):
        category = ("payments", "orders", "account", "delivery")[number % 4]
        topic_key = _choose_topic(rng, category)
        topic = TOPICS[topic_key]
        reason = topic["reason"]
        phrase = rng.choice(topic["phrases"])
        ticket_id = f"SUP-ARC-{number + 1:06d}"
        ticket = {"ticket_id": ticket_id, "category": category, "reason": reason,
                  "channel": rng.choice(CHANNELS),
                  "title": rng.choice([phrase.capitalize(), f"Question about {reason.replace('_', ' ')}",
                                       f"Help needed: {phrase}"]),
                  "body": (f"{phrase.capitalize()}. {rng.choice(['Could you check this?', 'I tried again once.', 'This happened today.', 'Please let me know what to do next.'])}"),
                  "customer_tier": rng.choice(["standard", "standard", "standard", "plus", "premium"]),
                  "locale": rng.choice(["en-US", "en-GB", "en-CA"])}
        evidence_id = _stable_id(seed, "ticket", number)
        record = {"evidence_id": evidence_id, "run_id": "synthetic_archive",
                  "simulated_at": _timestamp(rng), "available_at": None,
                  "source": "support_tickets", "event_type": "ticket_created",
                  "payload": ticket, "evaluation_topic": topic_key}
        record["available_at"] = record["simulated_at"]
        records.append(record)
        relevance[topic_key].append(evidence_id)
        source_counts["support_tickets"] += 1

    for number in range(COUNTS["product_logs"]):
        category = ("payments", "orders", "account", "delivery")[number % 4]
        service = SERVICES[category]
        topic_key = _choose_topic(rng, category)
        topic = TOPICS[topic_key]
        error_rate = rng.choice([0.0, 0.005, 0.01, 0.02, 0.03, 0.08, 0.18, 0.32])
        requests = rng.randint(80, 180)
        errors = min(requests, round(requests * error_rate))
        message = rng.choice(topic["phrases"])
        payload = {"service": service, "category": category, "requests": requests,
                   "errors": errors, "error_rate": round(errors / requests, 4),
                   "message": message, "region": rng.choice(["us-east", "us-west", "eu-west"]),
                   "deployment": f"dep-{rng.randrange(1, 81):04d}"}
        evidence_id = _stable_id(seed, "log", number)
        timestamp = _timestamp(rng)
        records.append({"evidence_id": evidence_id, "run_id": "synthetic_archive",
                        "simulated_at": timestamp, "available_at": timestamp,
                        "source": "product_logs", "event_type": "service_log_summary",
                        "payload": payload, "evaluation_topic": topic_key})
        relevance[topic_key].append(evidence_id)
        source_counts["product_logs"] += 1

    for number in range(COUNTS["issue_tracker"]):
        category = ("payments", "orders", "account", "delivery")[number % 4]
        topic_key = _choose_topic(rng, category)
        topic = TOPICS[topic_key]
        payload = {"issue_key": f"OPS-{2000 + number}", "category": category,
                   "service": SERVICES[category], "status": rng.choice(["open", "resolved", "monitoring"]),
                   "owner_team": f"{category.title()} Reliability",
                   "title": rng.choice(topic["queries"]).capitalize(),
                   "description": (f"Synthetic issue record. Symptoms include {rng.choice(topic['phrases'])}. "
                                  f"The service team is checking {topic['reason'].replace('_', ' ')} against request traces."),
                   "priority": rng.choice(["P2", "P2", "P3", "P4"]),
                   "updates": ["Reported by monitoring", "Owner reviewing diagnostic evidence"]}
        evidence_id = _stable_id(seed, "issue", number)
        timestamp = _timestamp(rng)
        records.append({"evidence_id": evidence_id, "run_id": "synthetic_archive",
                        "simulated_at": timestamp, "available_at": timestamp,
                        "source": "issue_tracker", "event_type": "issue_update",
                        "payload": payload, "evaluation_topic": topic_key})
        relevance[topic_key].append(evidence_id)
        source_counts["issue_tracker"] += 1

    for number in range(COUNTS["change_log"]):
        category = ("payments", "orders", "account", "delivery")[number % 4]
        topic_key = _choose_topic(rng, category)
        topic = TOPICS[topic_key]
        is_distractor = number % 11 == 0
        service = "catalog-search" if is_distractor else SERVICES[category]
        summary = ("Catalog search replica refresh; no checkout or payment service was changed."
                   if is_distractor else rng.choice(topic["queries"]).capitalize())
        payload = {"change_id": f"CHG-ARC-{number + 1:05d}", "service": service,
                   "category": category, "summary": summary,
                   "description": ("Routine planned change. Verify service ownership and affected path before assigning incident causality."
                                  if is_distractor else
                                  f"Synthetic {category} configuration or deployment record related to {topic['reason'].replace('_', ' ')}."),
                   "change_type": rng.choice(["deployment", "configuration", "dependency", "maintenance"]),
                   "status": rng.choice(["completed", "rolled_back", "monitoring"]),
                   "owner": f"{category.title()} Platform"}
        evidence_id = _stable_id(seed, "change", number)
        timestamp = _timestamp(rng)
        records.append({"evidence_id": evidence_id, "run_id": "synthetic_archive",
                        "simulated_at": timestamp, "available_at": timestamp,
                        "source": "change_log", "event_type": "change_record",
                        "payload": payload, "evaluation_topic": topic_key})
        relevance[topic_key].append(evidence_id)
        source_counts["change_log"] += 1

    records.sort(key=lambda item: (item["simulated_at"], item["evidence_id"]))
    corpus_path = OUTPUT / "records.jsonl"
    _write_jsonl(corpus_path, [{key: value for key, value in record.items() if key != "evaluation_topic"}
                               for record in records])

    docs_dir = OUTPUT / "knowledge_base"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for old in docs_dir.glob("*.md"):
        old.unlink()
    doc_relevance: dict[str, list[str]] = defaultdict(list)
    doc_count = 400
    sections_per_doc = 10
    for doc_number in range(doc_count):
        category = ("payments", "orders", "account", "delivery")[doc_number % 4]
        doc_type = ("service_guide", "historical_postmortem", "deployment_notes", "change_record_guide")[doc_number % 4]
        doc_id = f"KB-ARCHIVE-{doc_number + 1:04d}"
        available = (ARCHIVE_START + timedelta(days=rng.randrange(ARCHIVE_DAYS))).isoformat().replace("+00:00", "Z")
        service = SERVICES[category]
        sections = []
        for section_number in range(sections_per_doc):
            topic_key = _choose_topic(rng, category)
            topic = TOPICS[topic_key]
            heading = rng.choice(topic["queries"]).capitalize()
            paragraph = (
                f"For {service}, {rng.choice(topic['phrases'])}. Review request timestamps, service ownership, "
                f"and the matching operational record before attributing the symptom to a deployment. "
                f"The reference recommends comparing the relevant interval with an equal-length healthy period, "
                f"checking dependency responses, and noting any missing telemetry. This fictional guidance is "
                f"historical context; it does not establish that the same mechanism caused a later incident. "
                f"If the evidence remains ambiguous, preserve the uncertainty and collect a trace linked to the affected request."
            )
            sections.append((heading, paragraph, topic_key))

        frontmatter = ("---\n"
                       f"document_id: {doc_id}\n"
                       f"document_type: {doc_type}\n"
                       f"categories: {category}\n"
                       f"available_at: {available}\n"
                       "---\n\n")
        content = [frontmatter, f"# {category.title()} {doc_type.replace('_', ' ').title()} {doc_number + 1:04d}\n\n"]
        for section_number, (heading, paragraph, topic_key) in enumerate(sections, start=1):
            content.extend([f"## {heading}\n\n", f"{paragraph}\n\n"])
            doc_relevance[topic_key].append(
                _passage_id(f"{doc_id.casefold()}.md", section_number))
        (docs_dir / f"{doc_id.casefold()}.md").write_text("".join(content), encoding="utf-8")

    queries: list[dict[str, Any]] = []
    for topic_key, topic in TOPICS.items():
        relevant = list(dict.fromkeys([*relevance.get(topic_key, []),
                                       *doc_relevance.get(topic_key, [])]))
        for paraphrase_index, query in enumerate(topic["queries"]):
            queries.append({"query_id": f"Q-{len(queries) + 1:03d}", "query": query,
                            "category": topic["category"],
                            "relevant_record_ids": relevant,
                            "paraphrase_index": paraphrase_index})

    eval_dir = OUTPUT / "evaluation"
    eval_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(eval_dir / "queries.jsonl", queries)
    manifest = {"schema_version": 1, "seed": seed, "archive_start": ARCHIVE_START.isoformat().replace("+00:00", "Z"),
                "archive_days": ARCHIVE_DAYS, "structured_record_count": len(records),
                "record_counts_by_source": dict(sorted(source_counts.items())),
                "generated_markdown_documents": doc_count,
                "sections_per_document": sections_per_doc,
                "search_queries": len(queries), "evaluation_queries": "evaluation/queries.jsonl",
                "data_is_synthetic": True, "output": "records.jsonl"}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    sample_path = ROOT / "data" / "samples" / "synthetic_corpus_sample.jsonl"
    _write_jsonl(sample_path, [{key: value for key, value in record.items() if key != "evaluation_topic"}
                               for record in records[:10]])
    tracked_manifest = ROOT / "data" / "corpus_manifest.json"
    tracked_manifest.write_text(json.dumps({key: value for key, value in manifest.items()
                                            if key not in {"output", "evaluation_queries"}},
                                           indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    manifest = generate(args.seed)
    print(json.dumps(manifest, indent=2))
    print(f"Generated archive: {OUTPUT / 'records.jsonl'}")


if __name__ == "__main__":
    main()
