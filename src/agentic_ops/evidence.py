"""Bounded read-only evidence tools exposed to the investigator."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agentic_ops.store import EventStore


class EvidenceTools:
    _SERVICE_BY_CATEGORY = {
        "payments": "checkout-payments", "orders": "orders-api",
        "account": "account-api", "delivery": "delivery-api",
    }

    def __init__(self, store: EventStore):
        self.store = store

    @staticmethod
    def _focus_indices(observations_by_index: dict[int, dict[str, Any]],
                       focus_window_index: int) -> list[int]:
        """Return available windows from T-2 through T+1, in chronological order."""
        if focus_window_index not in observations_by_index:
            return []
        return [index for index in range(max(0, focus_window_index - 2), focus_window_index + 2)
                if index in observations_by_index]

    def build_episode_summary(self, run_id: str, category: str, start_window_index: int,
                              end_window_index: int, focus_window_index: int) -> dict[str, Any]:
        """Summarize the full alert episode and its available local focus-window group."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        scores = self.store.search_events(run_id, ("detector",), limit=1000,
                                          event_types=("window_score",))
        alerts = self.store.search_events(run_id, ("detector",), limit=1000,
                                          event_types=("anomaly_alert",))
        observation_by_index = {int(item["payload"]["window_index"]): item for item in observations}
        score_by_index = {int(item["payload"]["window_index"]): item for item in scores}
        alert_by_index = {int(item["payload"]["window_index"]): item for item in alerts
                          if item["payload"].get("category") == category}
        episode_indices = list(range(start_window_index, end_window_index + 1))
        if any(index not in observation_by_index or index not in alert_by_index for index in episode_indices):
            raise ValueError("Selected episode is incomplete in the stored run data.")

        def expected_count(index: int) -> float | None:
            result = score_by_index.get(index, {}).get("payload", {}).get("scores_by_category", {}).get(category, {})
            value = result.get("expected")
            return float(value) if value is not None else None

        def observed_count(index: int) -> int:
            return int(observation_by_index[index]["payload"].get("category_counts", {}).get(category, 0))

        def severity(alert: dict[str, Any]) -> float:
            values = alert["payload"].get("scores", [])
            return max((float(value.get("score", 0)) / float(value.get("threshold", 0))
                        for value in values if value.get("anomalous") and float(value.get("threshold", 0)) > 0),
                       default=0.0)

        strongest = max((alert_by_index[index] for index in episode_indices), key=severity)
        start_observation = observation_by_index[start_window_index]["payload"]
        end_observation = observation_by_index[end_window_index]["payload"]
        start_at = str(start_observation["window_start"])
        end_at = str(end_observation["window_end"])
        context_start_at = (datetime.fromisoformat(start_at.replace("Z", "+00:00")) - timedelta(hours=6))
        context_start_at = context_start_at.isoformat().replace("+00:00", "Z")
        change_start_at = (datetime.fromisoformat(start_at.replace("Z", "+00:00")) - timedelta(hours=48))
        change_start_at = change_start_at.isoformat().replace("+00:00", "Z")

        baseline_indices = list(range(start_window_index - len(episode_indices), start_window_index))
        baseline_indices = [index for index in baseline_indices if index in observation_by_index]
        episode_values = [observed_count(index) for index in episode_indices]
        episode_expected = [expected_count(index) for index in episode_indices]
        baseline_values = [observed_count(index) for index in baseline_indices]
        baseline_start_index = baseline_indices[0] if baseline_indices else None
        baseline_start_at = (observation_by_index[baseline_start_index]["payload"]["window_start"]
                             if baseline_start_index is not None else start_at)
        log_query_start = min(context_start_at, str(baseline_start_at))
        service = self._SERVICE_BY_CATEGORY.get(category, "")
        logs = self.store.search_events(run_id, ("product_logs",), service, 1000,
                                        ("service_log_summary",), log_query_start, end_at)

        def service_totals(items: list[dict[str, Any]], lower: str, upper: str) -> dict[str, Any]:
            selected = [item for item in items if lower <= item["simulated_at"] < upper]
            errors = sum(int(item["payload"].get("errors", 0)) for item in selected)
            requests = sum(int(item["payload"].get("requests", 0)) for item in selected)
            return {"errors": errors, "requests": requests,
                    "error_rate": round(errors / requests, 4) if requests else None,
                    "records": len(selected)}

        episode_logs = service_totals(logs, start_at, end_at)
        baseline_logs = service_totals(logs, str(baseline_start_at), start_at)
        focus_indices = self._focus_indices(observation_by_index, focus_window_index)
        if not focus_indices:
            raise ValueError("Selected focus window is unavailable in the stored run data.")
        focus_start_at = observation_by_index[focus_indices[0]]["payload"]["window_start"] if focus_indices else start_at
        focus_end_at = observation_by_index[focus_indices[-1]]["payload"]["window_end"] if focus_indices else end_at
        analysis_end_at = max(end_at, str(focus_end_at))
        logs = self.store.search_events(run_id, ("product_logs",), service, 1000,
                                        ("service_log_summary",), log_query_start, analysis_end_at)
        focus_logs = service_totals(logs, str(focus_start_at), str(focus_end_at))
        window_series = [{"window_index": index, "tickets": observed_count(index),
                          "expected": expected_count(index), "alerting": index in alert_by_index}
                         for index in episode_indices]
        focus_series = []
        for index in focus_indices:
            observation = observation_by_index[index]
            payload = observation["payload"]
            window_start = str(payload["window_start"])
            window_end = str(payload["window_end"])
            window_logs = service_totals(logs, window_start, window_end)
            focus_series.append({"window_index": index, "available": True,
                                 "start_at": window_start, "end_at": window_end,
                                 "tickets": observed_count(index), "expected": expected_count(index),
                                 "alerting": index in alert_by_index,
                                 "is_focus": index == focus_window_index,
                                 "service_errors": window_logs["errors"],
                                 "service_requests": window_logs["requests"],
                                 "service_error_rate": window_logs["error_rate"]})

        evidence_indices = list(dict.fromkeys((start_window_index, focus_window_index,
                                               int(strongest["payload"]["window_index"]), end_window_index)))
        evidence = [self._brief(score_by_index[index]) for index in evidence_indices if index in score_by_index]
        evidence.extend(self._brief(alert_by_index[index]) for index in evidence_indices if index in alert_by_index)
        return {
            "category": category, "start_window_index": start_window_index,
            "end_window_index": end_window_index, "focus_window_index": focus_window_index,
            "start_at": start_at, "end_at": end_at, "analysis_end_at": analysis_end_at,
            "context_start_at": context_start_at, "change_start_at": change_start_at,
            "alert_window_count": len(episode_indices), "duration_minutes": len(episode_indices) * 30,
            "total_tickets": sum(episode_values),
            "expected_total": round(sum(value for value in episode_expected if value is not None), 3),
            "expected_window_coverage": sum(value is not None for value in episode_expected),
            "average_tickets_per_window": round(sum(episode_values) / len(episode_values), 3),
            "baseline": {"window_count": len(baseline_indices), "expected_window_count": len(episode_indices),
                         "total_tickets": sum(baseline_values),
                         "average_tickets_per_window": round(sum(baseline_values) / len(baseline_values), 3)
                         if baseline_values else None,
                         "missing_windows": len(baseline_indices) < len(episode_indices)},
            "service_errors": {"episode": episode_logs, "baseline": baseline_logs},
            "strongest_window": {"window_index": int(strongest["payload"]["window_index"]),
                                 "tickets": int(strongest["payload"]["ticket_count"]),
                                 "severity_ratio": round(severity(strongest), 3)},
            "window_series": window_series,
            "focus": {"start_window_index": focus_indices[0],
                      "end_window_index": focus_indices[-1],
                      "start_at": focus_start_at, "end_at": focus_end_at,
                      "available_windows": len(focus_indices), "requested_windows": len(focus_indices),
                      "total_tickets": sum(observed_count(index) for index in focus_indices),
                      "expected_total": round(sum(value for index in focus_indices
                                                   if (value := expected_count(index)) is not None), 3),
                      "expected_window_coverage": sum(expected_count(index) is not None
                                                      for index in focus_indices),
                      "service_errors": focus_logs, "window_series": focus_series},
            "evidence": evidence,
        }

    def analyze_focus_window(self, run_id: str, category: str, focus_window_index: int,
                             start_window_index: int | None = None,
                             end_window_index: int | None = None) -> dict[str, Any]:
        """Analyze available windows from T-2 through T+1 in chronological order."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        observations_by_index = {int(item["payload"]["window_index"]): item for item in observations}
        default_indices = self._focus_indices(observations_by_index, focus_window_index)
        if not default_indices:
            return {"error": "The selected focus window is unavailable.",
                    "focus_window_index": focus_window_index, "requested_windows": 0,
                    "available_windows": 0, "windows": [], "evidence": []}
        requested_start = (start_window_index if start_window_index is not None else default_indices[0])
        requested_end = (end_window_index if end_window_index is not None else default_indices[-1])
        window_indices = [index for index in range(requested_start, requested_end + 1)
                          if index in observations_by_index]
        if focus_window_index not in window_indices:
            return {"error": "The selected focus window is outside the supplied focus group.",
                    "focus_window_index": focus_window_index, "requested_windows": 0,
                    "available_windows": 0, "windows": [], "evidence": []}
        scores = self.store.search_events(run_id, ("detector",), limit=1000,
                                          event_types=("window_score",))
        scores_by_index = {int(item["payload"]["window_index"]): item for item in scores}
        alerts = self.store.search_events(run_id, ("detector",), limit=1000,
                                          event_types=("anomaly_alert",))
        alert_indices = {int(item["payload"]["window_index"]) for item in alerts
                         if item["payload"].get("category") == category}
        rows = []
        evidence = []
        for index in window_indices:
            observation = observations_by_index[index]
            score = scores_by_index.get(index)
            category_score = (score or {}).get("payload", {}).get("scores_by_category", {}).get(category, {})
            payload = observation["payload"]
            rows.append({"window_index": index, "available": True,
                         "start_at": payload["window_start"], "end_at": payload["window_end"],
                         "tickets": int(payload.get("category_counts", {}).get(category, 0)),
                         "expected": category_score.get("expected"), "alerting": index in alert_indices,
                         "is_focus": index == focus_window_index, "ticket_records": 0,
                         "service_errors": 0, "service_requests": 0, "service_error_rate": None})
            evidence.append(self._brief(score or observation))
        available_rows = [row for row in rows if row["available"]]
        if not available_rows:
            return {"error": "No windows are available for the selected focus interval.",
                    "focus_window_index": focus_window_index, "requested_windows": len(window_indices),
                    "windows": rows, "evidence": []}
        start_at = available_rows[0]["start_at"]
        end_at = available_rows[-1]["end_at"]
        tickets = self.store.search_events(run_id, ("support_tickets",), limit=1000,
                                           event_types=("ticket_created",), start_at=start_at, end_at=end_at)
        ticket_groups: dict[str, int] = {}
        for ticket in tickets:
            payload = ticket["payload"]
            index = int(payload.get("window_index", -1))
            row = next((item for item in rows if item["window_index"] == index and item["available"]), None)
            if payload.get("category") == category and row is not None:
                row["ticket_records"] += 1
                reason = str(payload.get("reason", "unknown"))
                ticket_groups[reason] = ticket_groups.get(reason, 0) + 1
                evidence.append(self._brief(ticket))
        service = self._SERVICE_BY_CATEGORY.get(category, "")
        logs = self.store.search_events(run_id, ("product_logs",), service, 1000,
                                        ("service_log_summary",), start_at, end_at)
        errors = requests = 0
        for log in logs:
            stamp = log["simulated_at"]
            row = next((item for item in rows if item["available"]
                        and item["start_at"] <= stamp < item["end_at"]), None)
            if row is None:
                continue
            log_errors = int(log["payload"].get("errors", 0))
            log_requests = int(log["payload"].get("requests", 0))
            row["service_errors"] += log_errors
            row["service_requests"] += log_requests
            row["service_error_rate"] = round(log_errors / log_requests, 4) if log_requests else None
            errors += log_errors
            requests += log_requests
        evidence.extend(self._brief(item) for item in logs)
        return {"category": category, "focus_window_index": focus_window_index,
                "start_window_index": requested_start, "end_window_index": requested_end,
                "requested_windows": len(window_indices), "available_windows": len(available_rows), "windows": rows,
                "ticket_reasons": ticket_groups,
                "service_errors": {"errors": errors, "requests": requests,
                                   "error_rate": round(errors / requests, 4) if requests else None},
                "evidence": evidence}

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

    def search_tickets(self, run_id: str, query: str = "", limit: int = 20, category: str | None = None,
                       start_at: str | None = None, end_at: str | None = None) -> dict[str, Any]:
        records = self.store.search_events(run_id, ("support_tickets",), query, limit,
                                           ("ticket_created",), start_at, end_at)
        if category:
            records = [item for item in records if item["payload"].get("category") == category]
        return self._result(records, "tickets")

    def search_issues(self, run_id: str, query: str = "", limit: int = 20) -> dict[str, Any]:
        return self._result(self.store.search_events(run_id, ("issue_tracker",), query, limit), "issues")

    def search_product_logs(self, run_id: str, query: str = "", limit: int = 20,
                            start_at: str | None = None, end_at: str | None = None) -> dict[str, Any]:
        return self._result(self.store.search_events(run_id, ("product_logs",), query, limit,
                                                     ("service_log_summary",), start_at, end_at), "logs")

    def list_changes(self, run_id: str, service: str = "", limit: int = 20,
                     start_at: str | None = None, end_at: str | None = None) -> dict[str, Any]:
        records = self.store.search_events(run_id, ("change_log",), service, limit,
                                           ("change_record",), start_at, end_at)
        return self._result(records, "changes")

    def compare_categories(self, run_id: str, window_index: int,
                           end_window_index: int | None = None) -> dict[str, Any]:
        """Compare category counts in one window or aggregate an episode range."""
        records = self.store.search_events(run_id, ("detector",), limit=1000,
                                           event_types=("window_score",))
        end_index = window_index if end_window_index is None else end_window_index
        selected = [item for item in records
                    if window_index <= int(item["payload"].get("window_index", -1)) <= end_index]
        if not selected:
            return {"error": f"Window {window_index} has no completed detector scores.", "evidence": []}
        sums: dict[str, dict[str, Any]] = {}
        for item in selected:
            for category, result in item["payload"].get("scores_by_category", {}).items():
                row = sums.setdefault(category, {"observed": 0, "expected": 0.0,
                                                  "expected_windows": 0, "alert_windows": 0})
                row["observed"] += int(result.get("observed") or 0)
                if result.get("expected") is not None:
                    row["expected"] += float(result["expected"])
                    row["expected_windows"] += 1
                row["alert_windows"] += int(bool(result.get("alerted")))
        rows = [{"category": category, **values,
                 "expected": round(values["expected"], 3) if values["expected_windows"] else None}
                for category, values in sums.items()]
        return {"start_window_index": window_index, "end_window_index": end_index,
                "window_count": len(selected), "categories": rows,
                "evidence": [self._brief(item) for item in selected]}

    def rank_ticket_contributors(self, run_id: str, category: str,
                                 window_index: int, end_window_index: int | None = None) -> dict[str, Any]:
        """Rank ticket reasons and channels over a window or episode against an equal-length baseline."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        by_index = {int(item["payload"]["window_index"]): item for item in observations}
        end_index = window_index if end_window_index is None else end_window_index
        indices = [index for index in range(window_index, end_index + 1) if index in by_index]
        if not indices:
            return {"error": f"Window {window_index} is unavailable.", "contributors": [], "evidence": []}
        prior_indices = [index for index in range(window_index - len(indices), window_index) if index in by_index]
        query_start = (by_index[prior_indices[0]]["payload"]["window_start"]
                       if prior_indices else by_index[indices[0]]["payload"]["window_start"])
        query_end = by_index[indices[-1]]["payload"]["window_end"]
        tickets = self.store.search_events(run_id, ("support_tickets",), limit=1000,
                                           event_types=("ticket_created",), start_at=query_start, end_at=query_end)
        current = [item for item in tickets if window_index <= int(item["payload"].get("window_index", -1)) <= end_index
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
        return {"category": category, "start_window_index": window_index,
                "end_window_index": end_index, "window_count": len(indices),
                "category_ticket_count": len(current), "contributors": contributors[:8],
                "baseline_windows": len(prior_indices), "missing_baseline_windows": len(prior_indices) < len(indices),
                "evidence": list(evidence_by_id.values())[:100]}

    def build_incident_timeline(self, run_id: str, category: str, window_index: int,
                                end_window_index: int | None = None) -> dict[str, Any]:
        """Return records six hours before the range through its final window."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        by_index = {int(item["payload"]["window_index"]): item for item in observations}
        end_index = window_index if end_window_index is None else end_window_index
        target = by_index.get(window_index)
        final = by_index.get(end_index)
        if target is None or final is None:
            return {"error": f"Window {window_index} is unavailable.", "events": [], "evidence": []}
        end = datetime.fromisoformat(final["payload"]["window_end"].replace("Z", "+00:00"))
        start = datetime.fromisoformat(target["payload"]["window_start"].replace("Z", "+00:00")) - timedelta(hours=6)
        service = self._SERVICE_BY_CATEGORY.get(category, "")
        start_at = start.isoformat().replace("+00:00", "Z")
        end_at = end.isoformat().replace("+00:00", "Z")
        records = self.store.search_events(run_id,
            ("support_tickets", "product_logs", "issue_tracker", "change_log"), limit=1000,
            start_at=start_at, end_at=end_at)
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
        return {"category": category, "start_window_index": window_index, "end_window_index": end_index,
                "events": [self._brief(item) for item in selected[-60:]],
                "evidence": [self._brief(item) for item in selected[-60:]]}

    def compare_before_after(self, run_id: str, category: str,
                             window_index: int, end_window_index: int | None = None) -> dict[str, Any]:
        """Compare a range with an equal-duration preceding interval."""
        observations = self.store.search_events(run_id, ("detector",), limit=1000,
                                                event_types=("window_observation",))
        by_index = {int(item["payload"]["window_index"]): item for item in observations}
        end_index = window_index if end_window_index is None else end_window_index
        target = by_index.get(window_index)
        final = by_index.get(end_index)
        if target is None or final is None:
            return {"error": f"Window {window_index} is unavailable.", "evidence": []}
        start = datetime.fromisoformat(target["payload"]["window_start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(final["payload"]["window_end"].replace("Z", "+00:00"))
        interval = end - start
        before_start = start - interval
        before_start_at = before_start.isoformat().replace("+00:00", "Z")
        end_at = end.isoformat().replace("+00:00", "Z")
        tickets = self.store.search_events(run_id, ("support_tickets",), limit=1000,
                                           event_types=("ticket_created",), start_at=before_start_at, end_at=end_at)
        relevant_tickets = [item for item in tickets if item["payload"].get("category") == category]
        before_tickets = [item for item in relevant_tickets
                          if before_start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < start.isoformat()]
        during_tickets = [item for item in relevant_tickets
                          if start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < end.isoformat()]
        service = self._SERVICE_BY_CATEGORY.get(category, "")
        logs = self.store.search_events(run_id, ("product_logs",), service, 1000,
                                        ("service_log_summary",), before_start_at, end_at)
        before_logs = [item for item in logs if before_start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < start.isoformat()]
        during_logs = [item for item in logs if start.isoformat() <= item["simulated_at"].replace("Z", "+00:00") < end.isoformat()]
        def totals(items: list[dict[str, Any]]) -> dict[str, float | int | None]:
            errors = sum(int(item["payload"].get("errors", 0)) for item in items)
            requests = sum(int(item["payload"].get("requests", 0)) for item in items)
            return {"errors": errors, "requests": requests,
                    "error_rate": round(errors / requests, 4) if requests else None}
        evidence = before_tickets + during_tickets + before_logs + during_logs
        expected_baseline_windows = round(interval.total_seconds() / 1800)
        baseline_observations = [item for item in observations
                                 if before_start.isoformat() <= item["payload"]["window_start"].replace("Z", "+00:00")
                                 and item["payload"]["window_end"].replace("Z", "+00:00") <= start.isoformat()]
        baseline_windows = len(baseline_observations)
        return {"category": category, "start_window_index": window_index, "end_window_index": end_index,
                "before_minutes": round(interval.total_seconds() / 60),
                "during_minutes": round(interval.total_seconds() / 60),
                "before": {"ticket_count": round(len(before_tickets) / baseline_windows, 3) if baseline_windows else None,
                           "ticket_count_total": len(before_tickets),
                           "ticket_windows": baseline_windows,
                           "missing_windows": baseline_windows < expected_baseline_windows,
                           "service": totals(before_logs)},
                 "during": {"ticket_count": len(during_tickets),
                            "ticket_count_per_window": round(len(during_tickets) / max(1, end_index - window_index + 1), 3),
                            "service": totals(during_logs)},
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
