"""Bounded read-only evidence tools exposed to the investigator."""

from __future__ import annotations

from typing import Any

from agentic_ops.detectors import CountDetectorSuite, calibrated_config
from agentic_ops.store import EventStore


class EvidenceTools:
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

    @staticmethod
    def _brief(event: dict[str, Any] | None) -> dict[str, Any] | None:
        if event is None:
            return None
        return {"evidence_id": event["evidence_id"], "observed_at": event["simulated_at"],
                "source": event["source"], "event_type": event["event_type"], "payload": event["payload"]}

    @classmethod
    def _result(cls, records: list[dict[str, Any]], label: str) -> dict[str, Any]:
        return {label: [cls._brief(record) for record in records], "count": len(records)}
