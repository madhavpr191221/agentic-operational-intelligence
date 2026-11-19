"""Bounded read-only evidence tools exposed to the investigator."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agentic_ops.detectors import CountDetectorSuite, calibrated_config
from agentic_ops.store import EventStore


class EvidenceTools:
    _SERVICE_BY_CATEGORY = {
        "payments": "checkout-payments", "orders": "orders-api",
        "account": "account-api", "delivery": "delivery-api",
    }

    def __init__(self, store: EventStore):
        self.store = store

    def detection_summary(self, run_id: str) -> dict[str, Any]:
        events = self.store.search_events(run_id, ("detector",), limit=50)
        windows = [event for event in events if event["event_type"] == "window_score"]
        alerts = [event for event in events if event["event_type"] == "anomaly_alert"]
        latest = windows[-1] if windows else None
        evidence = []
        if latest:
            evidence.append({"evidence_id": latest["evidence_id"], "source": "detector",
                             "observed_at": latest["simulated_at"], "summary": "Latest window detector scores."})
        return {"alert_count": len(alerts), "alerts": [self._brief(e) for e in alerts[-5:]],
                "latest_window": self._brief(latest) if latest else None, "evidence": evidence}

    def run_anomaly_detection(self, run_id: str, window_index: int = -1,
                              model: str = "poisson") -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError("Unknown run")
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                               event_types=("window_observation",))
        if not observations:
            return {"error": "No completed windows are available yet.", "evidence": []}
        warmup = int(run["config"].get("calibration_windows", 48))
        suite = CountDetectorSuite(calibrated_config(model, warmup))
        target = observations[-1] if window_index < 0 else next(
            (event for event in observations if event["payload"].get("window_index") == window_index), None
        )
        if target is None:
            return {"error": f"Window {window_index} is not available.", "evidence": []}
        score_result = None
        for event in observations:
            scores, alert = suite.update(int(event["payload"]["ticket_count"]), int(event["payload"]["window_index"]))
            if event["evidence_id"] == target["evidence_id"]:
                score_result = {"window_index": event["payload"]["window_index"], "observed": event["payload"]["ticket_count"],
                                "scores": [score.to_dict() for score in scores], "alert": alert}
        return {"model": model, "result": score_result,
                "evidence": [{"evidence_id": target["evidence_id"], "source": "detector",
                              "observed_at": target["simulated_at"], "summary": "Observed ticket count for this window."}]}

    def search_tickets(self, run_id: str, query: str = "", limit: int = 20) -> dict[str, Any]:
        return self._result(self.store.search_events(run_id, ("support_tickets",), query, limit,
                                                     ("ticket_created",)), "tickets")

    def search_issues(self, run_id: str, query: str = "", limit: int = 20) -> dict[str, Any]:
        return self._result(self.store.search_events(run_id, ("issue_tracker",), query, limit), "issues")

    def search_product_logs(self, run_id: str, query: str = "", limit: int = 20) -> dict[str, Any]:
        return self._result(self.store.search_events(run_id, ("product_logs",), query, limit, ("service_log_summary",)), "logs")

    def list_changes(self, run_id: str, service: str = "", limit: int = 20) -> dict[str, Any]:
        records = self.store.search_events(run_id, ("change_log",), service, limit, ("change_record",))
        return self._result(records, "changes")

    def compare_categories(self, run_id: str, window_index: int) -> dict[str, Any]:
        """Compare observed category counts and detector expectations for one window."""
        records = self.store.search_events(run_id, ("detector",), limit=1000,
                                           event_types=("window_score",))
        target = next((item for item in records
                       if item["payload"].get("window_index") == window_index), None)
        if target is None:
            return {"error": f"Window {window_index} has no completed detector scores.", "evidence": []}
        rows = []
        for category, result in target["payload"].get("scores_by_category", {}).items():
            rows.append({"category": category, "observed": result.get("observed"),
                         "expected": result.get("expected"), "alerted": result.get("alerted")})
        return {"window_index": window_index, "categories": rows,
                "evidence": [self._brief(target)]}

    def rank_ticket_contributors(self, run_id: str, category: str,
                                 window_index: int) -> dict[str, Any]:
        """Rank message reasons and channels by excess over preceding per-window averages."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        target = next((item for item in observations
                       if item["payload"].get("window_index") == window_index), None)
        if target is None:
            return {"error": f"Window {window_index} is unavailable.", "contributors": [], "evidence": []}
        tickets = self.store.search_events(run_id, ("support_tickets",), limit=1000,
                                           event_types=("ticket_created",))
        start, end = target["payload"]["window_start"], target["payload"]["window_end"]
        prior_indices = [int(item["payload"]["window_index"]) for item in observations
                         if int(item["payload"]["window_index"]) < window_index][-48:]
        current = [item for item in tickets if start <= item["simulated_at"] < end
                   and item["payload"].get("category") == category]
        prior = [item for item in tickets
                 if item["payload"].get("category") == category
                 and int(item["payload"].get("window_index", -1)) in prior_indices]
        window_total = max(1, len(prior_indices))
        current_groups: dict[str, list[dict[str, Any]]] = {}
        prior_counts: dict[str, int] = {}
        for item in current:
            payload = item["payload"]
            for dimension in ("reason", "channel"):
                key = f"{dimension}:{payload.get(dimension, 'unknown')}"
                current_groups.setdefault(key, []).append(item)
        for item in prior:
            payload = item["payload"]
            for dimension in ("reason", "channel"):
                key = f"{dimension}:{payload.get(dimension, 'unknown')}"
                prior_counts[key] = prior_counts.get(key, 0) + 1
        contributors = []
        for key, items in current_groups.items():
            mean = prior_counts.get(key, 0) / window_total
            contributors.append({"dimension": key.split(":", 1)[0], "value": key.split(":", 1)[1],
                                 "observed": len(items), "baseline_per_window": round(mean, 3),
                                 "excess": round(len(items) - mean, 3),
                                 "evidence": [self._brief(item) for item in items[:12]]})
        contributors.sort(key=lambda item: (item["excess"], item["observed"]), reverse=True)
        tracked_contributors = {f"{group['dimension']}:{group['value']}" for group in contributors[:8]}
        baseline_evidence = [item for item in prior
                             if (f"reason:{item['payload'].get('reason', 'unknown')}" in tracked_contributors
                                 or f"channel:{item['payload'].get('channel', 'unknown')}" in tracked_contributors)]
        evidence_by_id = {item["evidence_id"]: item for group in contributors[:8]
                          for item in group["evidence"]}
        evidence_by_id.update({item["evidence_id"]: self._brief(item)
                               for item in baseline_evidence[:24]})
        return {"category": category, "window_index": window_index,
                "category_ticket_count": len(current), "contributors": contributors[:8],
                "baseline_windows": len(prior_indices),
                "evidence": list(evidence_by_id.values())[:100]}

    def build_incident_timeline(self, run_id: str, category: str,
                                window_index: int) -> dict[str, Any]:
        """Return observable records in the six hours before and during the selected window."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        target = next((item for item in observations
                       if item["payload"].get("window_index") == window_index), None)
        if target is None:
            return {"error": f"Window {window_index} is unavailable.", "events": [], "evidence": []}
        end = datetime.fromisoformat(target["payload"]["window_end"].replace("Z", "+00:00"))
        start = datetime.fromisoformat(target["payload"]["window_start"].replace("Z", "+00:00")) - timedelta(hours=6)
        service = self._SERVICE_BY_CATEGORY.get(category, "")
        records = self.store.search_events(run_id,
            ("support_tickets", "product_logs", "issue_tracker", "change_log"), limit=1000)
        selected = []
        for item in records:
            stamp = datetime.fromisoformat(item["simulated_at"].replace("Z", "+00:00"))
            if not start <= stamp <= end:
                continue
            payload = item["payload"]
            text = str(payload).casefold()
            if item["source"] == "support_tickets" and payload.get("category") != category:
                continue
            if item["source"] in {"product_logs", "issue_tracker", "change_log"} and service:
                if service.casefold() not in text and category.rstrip("s") not in text:
                    continue
            selected.append(item)
        selected.sort(key=lambda item: (item["simulated_at"], item["sequence"]))
        return {"category": category, "window_index": window_index,
                "events": [self._brief(item) for item in selected[-60:]],
                "evidence": [self._brief(item) for item in selected[-60:]]}

    def compare_before_after(self, run_id: str, category: str,
                             window_index: int) -> dict[str, Any]:
        """Compare ticket and service-error rates in the alert window with the prior six hours."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        target = next((item for item in observations
                       if item["payload"].get("window_index") == window_index), None)
        if target is None:
            return {"error": f"Window {window_index} is unavailable.", "evidence": []}
        start = datetime.fromisoformat(target["payload"]["window_start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(target["payload"]["window_end"].replace("Z", "+00:00"))
        before_start = start - timedelta(hours=6)
        tickets = self.store.search_events(run_id, ("support_tickets",), limit=1000,
                                           event_types=("ticket_created",))
        relevant_tickets = [item for item in tickets if item["payload"].get("category") == category]
        before_tickets = [item for item in relevant_tickets
                          if before_start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < start.isoformat()]
        during_tickets = [item for item in relevant_tickets
                          if start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < end.isoformat()]
        service = self._SERVICE_BY_CATEGORY.get(category, "")
        logs = self.store.search_events(run_id, ("product_logs",), service, 1000,
                                        ("service_log_summary",))
        before_logs = [item for item in logs if before_start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < start.isoformat()]
        during_logs = [item for item in logs if start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < end.isoformat()]
        def totals(items: list[dict[str, Any]]) -> dict[str, float | int | None]:
            errors = sum(int(item["payload"].get("errors", 0)) for item in items)
            requests = sum(int(item["payload"].get("requests", 0)) for item in items)
            return {"errors": errors, "requests": requests,
                    "error_rate": round(errors / requests, 4) if requests else None}
        evidence = before_tickets + during_tickets + before_logs + during_logs
        expected_baseline_windows = round((start - before_start).total_seconds() / 1800)
        baseline_observations = [item for item in observations
                                 if before_start.isoformat() <= item["payload"]["window_start"].replace("Z", "+00:00")
                                 and item["payload"]["window_end"].replace("Z", "+00:00") <= start.isoformat()]
        baseline_windows = len(baseline_observations)
        return {"category": category, "before_hours": 6, "during_minutes": 30,
                "before": {"ticket_count": round(len(before_tickets) / baseline_windows, 3) if baseline_windows else None,
                           "ticket_count_total": len(before_tickets),
                           "ticket_windows": baseline_windows,
                           "missing_windows": baseline_windows < expected_baseline_windows,
                           "service": totals(before_logs)},
                "during": {"ticket_count": len(during_tickets), "service": totals(during_logs)},
                "missing_before_logs": not before_logs, "missing_during_logs": not during_logs,
                "evidence": [self._brief(item) for item in
                             (baseline_observations + [target] + evidence)[-100:]]}

    def suggest_runbook_actions(self, run_id: str, category: str,
                                evidence_ids: list[str] | None = None) -> dict[str, Any]:
        """Return non-executing next steps only when supplied evidence supports a relevant runbook."""
        records = self.store.get_events_by_evidence_ids(run_id, (evidence_ids or [])[:20])
        selected = [item for item in records if
                    (item["source"] == "support_tickets" and item["payload"].get("category") == category)
                    or category.rstrip("s") in str(item["payload"]).casefold()
                    or self._SERVICE_BY_CATEGORY.get(category, "").casefold() in str(item["payload"]).casefold()]
        latest_issue_state: dict[str, str] = {}
        for item in selected:
            if item["source"] == "issue_tracker":
                key = str(item["payload"].get("issue_key", item["evidence_id"]))
                latest_issue_state[key] = str(item["payload"].get("status", "unknown"))
        active_payment_issue = any(status == "open" for status in latest_issue_state.values())
        payment_errors = any(item["source"] == "product_logs"
                             and item["payload"].get("errors", 0) > 0 for item in selected)
        if category == "payments" and active_payment_issue and payment_errors:
            has_change = any(item["source"] == "change_log" for item in selected)
            action = {"runbook_id": "RB-PAY-01", "title": "Review payment authorization failures",
                      "next_step": ("Compare current gateway retry settings with the cited recent change; follow the team's payment runbook to decide whether rollback or escalation is appropriate."
                                    if has_change else
                                    "Have the payments owner review the open issue and error logs against the payment runbook before deciding on remediation."),
                      "verification": "Check authorization error rate and payment ticket arrivals after any approved change.",
                      "evidence_ids": [item["evidence_id"] for item in selected]}
        elif selected and category != "payments":
            action = {"runbook_id": f"RB-{category.upper()}-01", "title": f"Inspect the {category} service",
                      "next_step": f"Have the owning team review the cited {category} records and its current runbook before changing configuration.",
                      "verification": "Confirm the relevant service metric returns to its expected range and customer contacts decline.",
                      "evidence_ids": [item["evidence_id"] for item in selected]}
        else:
            action = {"runbook_id": None, "title": "Gather more evidence",
                      "next_step": f"Collect time-matched service logs and issue/change records for {category} before recommending a remediation.",
                      "verification": "Re-run the investigation when time-aligned evidence is available.",
                      "evidence_ids": []}
        return {"category": category, "actions": [action],
                "evidence": [self._brief(item) for item in selected]}

    @staticmethod
    def _brief(event: dict[str, Any] | None) -> dict[str, Any] | None:
        if event is None:
            return None
        return {"evidence_id": event["evidence_id"], "observed_at": event["simulated_at"],
                "source": event["source"], "event_type": event["event_type"], "payload": event["payload"]}

    @classmethod
    def _result(cls, records: list[dict[str, Any]], label: str) -> dict[str, Any]:
        return {label: [cls._brief(record) for record in records], "count": len(records)}
