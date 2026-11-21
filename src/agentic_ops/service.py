"""Run orchestration and investigation jobs."""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from agentic_ops.agent import Investigator, MockInvestigator, OpenAIInvestigator
from agentic_ops.detection import (
    CategoryDetectorSuite,
    CountObservation,
)
from agentic_ops.domain import CATEGORIES, SimulationConfig
from agentic_ops.detector_host import DetectorHost
from agentic_ops.incident_worker import IncidentWorker
from agentic_ops.evidence import EvidenceTools
from agentic_ops.simulation import GeneratedEvent, GeneratedWindow, generate_live_week, generate_run
from agentic_ops.retrieval import load_knowledge_passages, load_runbook_pages
from agentic_ops.search_activity import summarize_search_trace
from agentic_ops.store import EventStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_openai_key_from_dotenv() -> None:
    """Load only the OpenAI credential, leaving other .env secrets untouched."""
    if os.getenv("OPENAI_API_KEY"):
        return
    values = dotenv_values(REPOSITORY_ROOT / ".env")
    api_key = values.get("OPENAI_API_KEY")
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key


@dataclass
class RunControl:
    condition: threading.Condition = field(default_factory=threading.Condition)
    paused: bool = False
    stopped: bool = False
    step_budget: int = 0
    pause_started_at: float | None = None
    paused_seconds: float = 0.0
    thread: threading.Thread | None = None


class OperationsRuntime:
    def __init__(self, store: EventStore | None = None, investigator: Investigator | None = None):
        self.store = store or EventStore()
        # A replay's clock and controls live in this process. Persisted running
        # rows cannot be resumed safely after a process restart.
        self.store.stop_interrupted_runs()
        self.evidence = EvidenceTools(self.store)
        # Initialize the additive incident/outbox schema even before a replay exists.
        DetectorHost(self.store, "schema-bootstrap", CategoryDetectorSuite("poisson", 48, CATEGORIES))
        self._provided_investigator = investigator
        self._controls: dict[str, RunControl] = {}
        self._lock = threading.Lock()
        self.incident_worker = IncidentWorker(self.store, self._investigator, self.evidence)
        self.incident_worker.start()

    @property
    def investigator_mode(self) -> str:
        if self._provided_investigator is not None:
            return getattr(self._provided_investigator, "mode", "custom")
        return "offline_demo" if os.getenv("AGENT_MODE", "openai").casefold() == "mock" else "openai"

    @property
    def openai_key_configured(self) -> bool:
        """Report key presence without exposing or loading the credential."""
        if self._provided_investigator is not None:
            return getattr(self._provided_investigator, "mode", "") == "openai"
        if os.getenv("AGENT_MODE", "openai").casefold() == "mock":
            return False
        if os.getenv("OPENAI_API_KEY", "").strip():
            return True
        values = dotenv_values(REPOSITORY_ROOT / ".env")
        return bool(str(values.get("OPENAI_API_KEY") or "").strip())

    def _investigator(self) -> Investigator:
        if self._provided_investigator is not None:
            return self._provided_investigator
        mode = os.getenv("AGENT_MODE", "openai").casefold()
        if mode == "mock":
            return MockInvestigator()
        _load_openai_key_from_dotenv()
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("Set OPENAI_API_KEY or choose AGENT_MODE=mock for an offline demo.")
        return OpenAIInvestigator()

    def start_run(self, config: SimulationConfig, investigation_mode: str = "automatic") -> dict[str, Any]:
        if investigation_mode not in {"automatic", "manual"}:
            raise ValueError("Investigation mode must be automatic or manual")
        run_config = asdict(config)
        run_config["investigation_mode"] = investigation_mode
        run_id = self.store.create_run(config.scenario, config.seed, config.arrival_model, run_config)
        control = RunControl()
        with self._lock:
            self._controls[run_id] = control
        truth_by_scenario = {
            "payment_issue": "payment_authorization_failure",
            "orders_queue_backlog": "order_worker_queue_backlog",
            "delivery_carrier_degradation": "carrier_api_degradation",
            "unrelated_issue": "unrelated_search_issue",
            "resolved_issue": "resolved_payment_issue",
            "healthy": "none",
        }
        has_incident = config.scenario != "healthy"
        truth = {"scenario": config.scenario,
                 "cause": truth_by_scenario.get(config.scenario, "none"),
                 "issue_opens_at_window": ((config.incident_start_window if config.incident_start_window is not None
                                             else min(config.calibration_windows, config.window_count // 3))
                                            if has_incident else None),
                 "severe_failure_starts_at_window": ((config.incident_start_window if config.incident_start_window is not None
                                                       else config.window_count - 4)
                                                      if config.incident_severity == "severe" and has_incident else None),
                 "incident_severity": config.incident_severity}
        self.store.add_truth(run_id, truth)
        start = "2026-01-01T00:00:00Z"
        for page in load_runbook_pages():
            self.store.add_event(run_id, start, "runbooks", "runbook_page", page)
        for passage in load_knowledge_passages():
            self.store.add_event(run_id, passage["available_at"], "knowledge_docs",
                                 "document_passage", passage)
        self.store.set_run_status(run_id, "preparing" if config.monitor_history_windows else "running")
        self.store.add_event(run_id, start, "system", "run_started",
                             {"scenario": config.scenario, "seed": config.seed, "arrival_model": config.arrival_model,
                              "window_count": config.window_count,
                              "streaming_week": bool(config.monitor_history_windows),
                              "playback_duration_seconds": config.playback_duration_seconds})
        thread = threading.Thread(target=self._run_simulation, args=(run_id, config, control), daemon=True,
                                  name=f"simulation-{run_id[:8]}")
        control.thread = thread
        thread.start()
        return self.store.get_run(run_id) or {"run_id": run_id}

    def _run_simulation(self, run_id: str, config: SimulationConfig, control: RunControl) -> None:
        detector_host = DetectorHost(
            self.store, run_id,
            CategoryDetectorSuite(config.arrival_model, config.calibration_windows, CATEGORIES),
        )
        try:
            automatic = self._run_investigation_mode(run_id) == "automatic"
            detector_host.deliver_pending(automatic=automatic)
            if config.monitor_history_windows:
                self._run_live_week(run_id, config, control, detector_host, automatic)
                return
            for window in generate_run(config):
                with control.condition:
                    while control.paused and control.step_budget == 0 and not control.stopped:
                        control.condition.wait(timeout=0.5)
                    if control.stopped:
                        self.store.set_run_status(run_id, "stopped")
                        return
                    if control.step_budget > 0:
                        control.step_budget -= 1

                for generated in window.events:
                    self.store.add_event(run_id, generated.simulated_at, generated.source,
                                         generated.event_type, generated.payload)
                self._process_window(run_id, config, window, detector_host, automatic)
                if config.tick_delay_seconds > 0:
                    time.sleep(config.tick_delay_seconds)
            self.store.set_run_status(run_id, "completed")
        except Exception as exc:  # keep a visible error state for the local operator
            self.store.set_run_status(run_id, "failed", str(exc))

    def _store_generated_event(self, run_id: str, generated: GeneratedEvent,
                               replay_phase: str | None = None) -> dict[str, Any]:
        payload = dict(generated.payload)
        if replay_phase is not None:
            payload["replay_phase"] = replay_phase
        return self.store.add_event(run_id, generated.simulated_at, generated.source,
                                    generated.event_type, payload)

    def _process_window(self, run_id: str, config: SimulationConfig, window: GeneratedWindow,
                        detector_host: DetectorHost, automatic: bool) -> None:
        observation = self.store.add_event(
            run_id, window.end, "detector", "window_observation",
            {"schema_version": 1, "window_index": window.index,
             "window_start": window.start, "window_end": window.end,
             "ticket_count": window.ticket_count, "category_counts": window.category_counts},
        )
        count_observation = CountObservation(
            window_index=window.index, window_start=window.start, window_end=window.end,
            category_counts=window.category_counts,
        )
        detection = detector_host.process(count_observation)
        score_event = self.store.add_event(
            run_id, window.end, "detector", "window_score",
            {"schema_version": 1, "window_index": window.index,
             "observed": window.ticket_count, "category_counts": window.category_counts,
             "scores_by_category": detection["categories"],
             "calibration": window.index < config.calibration_windows,
             "observation_evidence_id": observation["evidence_id"],
             "detection_result": detection},
        )
        transition_by_category = {
            event["category"]: event["episode"]
            for event in detector_host.pending_events()
            if event["window"]["window_index"] == window.index
            and event["event_type"] in {"episode_opened", "episode_updated"}
        }
        for category, result in detection["categories"].items():
            if not result["alerted"]:
                continue
            scores = result["scores"]
            triggered = [score["detector"] for score in scores if score["anomalous"]]
            by_detector = {score["detector"]: score for score in scores}
            likelihood_name = ("negative_binomial_llr" if config.arrival_model == "negative_binomial"
                               else "poisson_llr")
            signal = {
                "metric": "support_ticket_arrivals", "category": category,
                "window_index": window.index, "window_start": window.start,
                "window_end": window.end, "window_minutes": 30,
                "observed": result["observed"], "expected": result["expected"],
                "arrival_model": config.arrival_model,
                "scores": {
                    "z_score": by_detector["standardized_residual"]["score"],
                    "cusum": by_detector["cusum"]["score"],
                    "log_likelihood_ratio": by_detector[likelihood_name]["score"],
                },
            }
            self.store.add_event(run_id, window.end, "detector", "anomaly_alert",
                                 {"schema_version": 1, "metric": "support_ticket_arrivals",
                                  "category": category, "window_index": window.index,
                                  "window_start": window.start, "window_end": window.end,
                                  "ticket_count": result["observed"], "expected": result["expected"],
                                  "signal": signal, "scores": scores,
                                  "statistical_episode": transition_by_category.get(category),
                                  "detectors_triggered": triggered,
                                  "observation_evidence_id": observation["evidence_id"],
                                  "window_score_evidence_id": score_event["evidence_id"]})
        detector_host.deliver_pending(automatic=automatic)

    def _run_live_week(self, run_id: str, config: SimulationConfig, control: RunControl,
                       detector_host: DetectorHost, automatic: bool) -> None:
        windows = generate_live_week(config)
        history_end = config.monitor_history_windows
        history = windows[:history_end]
        stream = windows[history_end:]

        # History is genuinely in the past at stream start, so it can be loaded
        # up front and used to warm the existing detector without creating alerts.
        for window in history:
            for generated in window.events:
                self._store_generated_event(run_id, generated, "history")
            self._process_window(run_id, config, window, detector_host, automatic)

        baseline = detector_host.monitoring_snapshot()
        self.store.add_event(
            run_id, history[-1].end if history else "2026-01-01T00:00:00Z",
            "detector", "detector_baseline_ready" if baseline["baseline_ready"] else "detector_baseline_status",
            {**baseline, "history_start": history[0].start if history else None,
             "history_end": history[-1].end if history else None},
        )

        if not stream:
            self.store.set_run_status(run_id, "completed")
            return

        playback_seconds = config.playback_duration_seconds
        if playback_seconds <= 0:
            raise ValueError("Live playback duration must be positive")
        stream_start = datetime.fromisoformat(stream[0].start.replace("Z", "+00:00"))
        stream_end = datetime.fromisoformat(stream[-1].end.replace("Z", "+00:00"))
        simulated_span_seconds = (stream_end - stream_start).total_seconds()
        if simulated_span_seconds <= 0:
            raise ValueError("Live replay stream must have a positive simulated duration")

        with control.condition:
            self.store.set_run_status(run_id, "paused" if control.paused else "running")
        wall_start = time.monotonic()
        self._emit_replay_progress(run_id, stream_start, history_end,
                                   {category: 0 for category in CATEGORIES}, 0.0)
        last_progress = wall_start

        for window in stream:
            current_counts = {category: 0 for category in CATEGORIES}
            window_end = datetime.fromisoformat(window.end.replace("Z", "+00:00"))
            stepped = False
            for generated in window.events:
                event_time = datetime.fromisoformat(generated.simulated_at.replace("Z", "+00:00"))
                target_offset = (event_time - stream_start).total_seconds()
                target_wall_elapsed = playback_seconds * target_offset / simulated_span_seconds
                if not stepped:
                    state, last_progress = self._wait_for_live_time(
                        control, wall_start, target_wall_elapsed, run_id, stream_start,
                        simulated_span_seconds, playback_seconds, window.index,
                        current_counts, last_progress,
                    )
                    if state == "stopped":
                        self.store.set_run_status(run_id, "stopped")
                        return
                    if state == "step":
                        stepped = True
                        last_progress = time.monotonic()
                self._store_generated_event(run_id, generated, "stream")
                if generated.event_type == "ticket_created":
                    category = str(generated.payload.get("category", ""))
                    if category in current_counts:
                        current_counts[category] += 1

            target_offset = (window_end - stream_start).total_seconds()
            target_wall_elapsed = playback_seconds * target_offset / simulated_span_seconds
            if not stepped:
                state, last_progress = self._wait_for_live_time(
                    control, wall_start, target_wall_elapsed, run_id, stream_start,
                    simulated_span_seconds, playback_seconds, window.index,
                    current_counts, last_progress,
                )
                if state == "stopped":
                    self.store.set_run_status(run_id, "stopped")
                    return
                if state == "step":
                    stepped = True
            if current_counts != window.category_counts:
                raise RuntimeError(
                    f"Released ticket counts for window {window.index} do not match the generated count observation"
                )
            self._process_window(run_id, config, window, detector_host, automatic)
            completed = window.index - history_end + 1
            percent = completed / len(stream)
            self._emit_replay_progress(run_id, window_end, window.index + 1,
                                       {category: 0 for category in CATEGORIES}, percent)
            last_progress = time.monotonic()
            if stepped:
                with control.condition:
                    control.paused = True
                    if control.pause_started_at is None:
                        control.pause_started_at = time.monotonic()
                    self.store.set_run_status(run_id, "paused")

        self._emit_replay_progress(run_id, stream_end, config.window_count,
                                   {category: 0 for category in CATEGORIES}, 1.0)
        self.store.set_run_status(run_id, "completed")

    def _wait_for_live_time(self, control: RunControl, wall_start: float, target_elapsed: float,
                            run_id: str, stream_start: datetime, simulated_span_seconds: float,
                            playback_seconds: float, window_index: int,
                            current_counts: dict[str, int],
                            last_progress: float) -> tuple[str, float]:
        while True:
            with control.condition:
                if control.stopped:
                    return "stopped", last_progress
                if control.paused:
                    if control.step_budget > 0:
                        control.step_budget -= 1
                        return "step", last_progress
                    control.condition.wait(timeout=0.05)
                    continue
                now = time.monotonic()
                elapsed = now - wall_start - control.paused_seconds
                remaining = target_elapsed - elapsed
                if remaining <= 0:
                    return "ready", last_progress
                control.condition.wait(timeout=min(remaining, 0.05))

            now = time.monotonic()
            if now - last_progress >= 0.25:
                elapsed = max(0.0, now - wall_start - control.paused_seconds)
                fraction = min(1.0, elapsed / playback_seconds)
                simulated_at = stream_start + timedelta(seconds=simulated_span_seconds * fraction)
                self._emit_replay_progress(run_id, simulated_at, window_index,
                                           current_counts, fraction)
                last_progress = now

    def _emit_replay_progress(self, run_id: str, simulated_at: datetime, current_window_index: int,
                              current_counts: dict[str, int], progress: float) -> None:
        self.store.add_event(
            run_id, simulated_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "replay", "replay_progress",
            {"simulated_at": simulated_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
             "current_window_index": current_window_index,
             "current_window_counts": dict(current_counts),
             "progress": min(1.0, max(0.0, progress))},
        )

    def _run_investigation_mode(self, run_id: str) -> str:
        run = self.store.get_run(run_id)
        return str((run or {}).get("config", {}).get("investigation_mode", "automatic"))

    def control_run(self, run_id: str, action: str) -> dict[str, Any]:
        with self._lock:
            control = self._controls.get(run_id)
        run = self.store.get_run(run_id)
        if control is None or run is None:
            raise KeyError("Unknown run")
        if run["status"] in {"completed", "failed", "stopped", "stopping"}:
            raise ValueError("Run is already terminal")
        with control.condition:
            if action == "pause":
                if not control.paused:
                    control.paused = True
                    control.pause_started_at = time.monotonic()
                self.store.set_run_status(run_id, "paused")
            elif action == "resume":
                if control.paused and control.pause_started_at is not None:
                    control.paused_seconds += time.monotonic() - control.pause_started_at
                    control.pause_started_at = None
                control.paused = False
                self.store.set_run_status(run_id, "running")
                control.condition.notify_all()
            elif action == "step":
                if not control.paused:
                    control.paused = True
                    control.pause_started_at = time.monotonic()
                control.step_budget += 1
                self.store.set_run_status(run_id, "paused")
                control.condition.notify_all()
            elif action == "stop":
                control.stopped = True
                if control.paused and control.pause_started_at is not None:
                    control.paused_seconds += time.monotonic() - control.pause_started_at
                    control.pause_started_at = None
                control.paused = False
                self.store.set_run_status(run_id, "stopping")
                control.condition.notify_all()
            else:
                raise ValueError("Action must be pause, resume, step, or stop")
        return {"run_id": run_id, "action": action, "status": (self.store.get_run(run_id) or {}).get("status")}

    def start_investigation(self, run_id: str, question: str,
                            alert_evidence_id: str | None = None,
                            alert_evidence_ids: list[str] | None = None,
                            episode_selections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if self.store.get_run(run_id) is None:
            raise KeyError("Unknown run")
        alerts = self.store.search_events(run_id, ("detector",), limit=1000,
                                          event_types=("anomaly_alert",))
        if episode_selections and (alert_evidence_id or alert_evidence_ids):
            raise ValueError("Use episode_selections or legacy alert evidence IDs, not both")
        if alert_evidence_id and alert_evidence_ids:
            raise ValueError("Use either alert_evidence_id or alert_evidence_ids, not both")
        alerts_by_id = {event["evidence_id"]: event for event in alerts}
        selections: list[dict[str, Any]] = []
        if episode_selections:
            if len(episode_selections) > len(CATEGORIES):
                raise ValueError("Select at most one episode for each of four categories")
            selected_categories = [str(item.get("category", "")) for item in episode_selections]
            if any(category not in CATEGORIES for category in selected_categories):
                raise ValueError("An episode has an unsupported category")
            if len(set(selected_categories)) != len(selected_categories):
                raise ValueError("Select no more than one alert episode per category")
            for item in episode_selections:
                category = str(item["category"])
                start_index = int(item["start_window_index"])
                end_index = int(item["end_window_index"])
                focus_index = int(item["focus_window_index"])
                if start_index > end_index or not start_index <= focus_index <= end_index:
                    raise ValueError("Episode bounds must contain the selected focus window")
                category_alerts = sorted(
                    (alert for alert in alerts if alert["payload"].get("category") == category),
                    key=lambda alert: int(alert["payload"]["window_index"]))
                indices = [int(alert["payload"]["window_index"]) for alert in category_alerts]
                requested = list(range(start_index, end_index + 1))
                if [index for index in indices if start_index <= index <= end_index] != requested:
                    raise ValueError("Episode bounds must match a continuous run of alert windows")
                if start_index - 1 in indices or end_index + 1 in indices:
                    raise ValueError("Episode bounds must include every consecutive alert window")
                focus_alert = next((alert for alert in category_alerts
                                    if int(alert["payload"]["window_index"]) == focus_index), None)
                if focus_alert is None:
                    raise ValueError("Focus window must be an alert window in the selected episode")
                selections.append({"category": category, "start_window_index": start_index,
                                   "end_window_index": end_index, "focus_window_index": focus_index,
                                   "focus_alert": focus_alert})
        else:
            selected_ids = alert_evidence_ids or ([alert_evidence_id] if alert_evidence_id else [])
            if selected_ids:
                if len(selected_ids) > len(CATEGORIES) or len(set(selected_ids)) != len(selected_ids):
                    raise ValueError("Select one alert window per category, up to four categories")
                selected_alerts = [alerts_by_id.get(evidence_id) for evidence_id in selected_ids]
                if any(alert is None for alert in selected_alerts):
                    raise ValueError("Every selected alert must belong to this run")
                selected_categories = [str(alert["payload"].get("category", "")) for alert in selected_alerts]
                if any(category not in CATEGORIES for category in selected_categories):
                    raise ValueError("A selected alert has an unsupported category")
                if len(set(selected_categories)) != len(selected_categories):
                    raise ValueError("Select no more than one alert episode per category")
            else:
                selected_alerts = [alerts[-1]] if alerts else []
            for alert in selected_alerts:
                index = int(alert["payload"]["window_index"])
                selections.append({"category": str(alert["payload"]["category"]),
                                   "start_window_index": index, "end_window_index": index,
                                   "focus_window_index": index, "focus_alert": alert})
        investigation_id = self.store.create_investigation(run_id, question)
        selected_signals = []
        for selection in selections:
            summary = self.evidence.build_episode_summary(
                run_id, selection["category"], selection["start_window_index"],
                selection["end_window_index"], selection["focus_window_index"])
            alert = selection["focus_alert"]
            selected_signals.append({
                "evidence_id": alert["evidence_id"], "simulated_at": alert["simulated_at"],
                **alert["payload"], "episode_start_window_index": selection["start_window_index"],
                "episode_end_window_index": selection["end_window_index"],
                "focus_window_index": selection["focus_window_index"], "episode_summary": summary,
            })
        context = ({"selected_signals": selected_signals,
                    "categories": [signal["category"] for signal in selected_signals],
                    "episode_summaries": [signal["episode_summary"] for signal in selected_signals],
                    **(selected_signals[0] if len(selected_signals) == 1 else {})}
                   if selected_signals else None)
        worker = threading.Thread(target=self._investigate,
                                  args=(investigation_id, run_id, question, context), daemon=True,
                                  name=f"investigation-{investigation_id[:8]}")
        worker.start()
        return self.store.get_investigation(investigation_id) or {"investigation_id": investigation_id}

    def _investigate(self, investigation_id: str, run_id: str, question: str,
                     alert_context: dict[str, Any] | None) -> None:
        self.store.set_investigation(investigation_id, "running")
        try:
            report = self._investigator().investigate(
                run_id, question, self.evidence, alert_context,
                on_progress=lambda event: self.store.add_investigation_event(investigation_id, event),
            )
            # Persist the deterministic scope summary with every outcome. Some investigator
            # paths (for example, an evidence-free abstention) return before composing it.
            canonical_summaries = [
                signal["episode_summary"]
                for signal in (alert_context or {}).get("selected_signals", [])
                if isinstance(signal.get("episode_summary"), dict)
            ]
            if canonical_summaries:
                report.episode_summaries = canonical_summaries
            report.search_activity = summarize_search_trace(report.tool_trace)
            self.store.set_investigation(investigation_id, report.status, report.to_dict())
        except Exception as exc:
            self.store.add_investigation_event(
                investigation_id,
                {"type": "failed", "stage": "failed", "message": "The investigation could not be completed."},
            )
            self.store.set_investigation(investigation_id, "failed", error=str(exc))

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        """Return an investigation, backfilling deterministic summaries for older reports."""
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            return None
        report = investigation.get("report")
        if not isinstance(report, dict):
            return investigation

        if "search_activity" not in report:
            report["search_activity"] = summarize_search_trace(report.get("tool_trace", []))
        if report.get("episode_summaries"):
            return investigation

        categories = list(dict.fromkeys(
            str(item.get("category")) for item in report.get("category_findings", [])
            if isinstance(item, dict) and item.get("category") in CATEGORIES
        ))
        if not categories:
            return investigation

        trace = report.get("tool_trace", [])
        summaries = []
        alerts = self.store.search_events(investigation["run_id"], ("detector",), limit=1000,
                                          event_types=("anomaly_alert",))
        alerts_by_category: dict[str, set[int]] = {}
        for alert in alerts:
            category = alert["payload"].get("category")
            if category in categories:
                alerts_by_category.setdefault(str(category), set()).add(
                    int(alert["payload"]["window_index"]))

        for category in categories:
            category_indices: list[int] = []
            fallback_indices: list[int] = []
            for entry in trace if isinstance(trace, list) else []:
                if not isinstance(entry, dict):
                    continue
                result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
                arguments = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
                index_value = next((source.get(key) for source in (result, arguments)
                                    for key in ("focus_window_index", "window_index")
                                    if source.get(key) is not None), None)
                if index_value is None:
                    continue
                try:
                    index = int(index_value)
                except (TypeError, ValueError):
                    continue
                entry_category = result.get("category") or arguments.get("category")
                if entry_category == category:
                    category_indices.append(index)
                elif entry.get("tool") == "compare_categories" and any(
                        isinstance(item, dict) and item.get("category") == category
                        for item in result.get("categories", [])):
                    fallback_indices.append(index)

            candidate_indices = category_indices or (
                fallback_indices if len(set(fallback_indices)) == 1 else [])
            focus_index = next((index for index in reversed(candidate_indices)
                                if index in alerts_by_category.get(category, set())), None)
            if focus_index is None:
                continue
            alert_indices = alerts_by_category[category]
            start_index = end_index = focus_index
            while start_index - 1 in alert_indices:
                start_index -= 1
            while end_index + 1 in alert_indices:
                end_index += 1
            try:
                summaries.append(self.evidence.build_episode_summary(
                    investigation["run_id"], category, start_index, end_index, focus_index))
            except (KeyError, ValueError):
                continue

        if summaries:
            report["episode_summaries"] = summaries
            self.store.set_investigation(investigation_id, investigation["status"], report,
                                         investigation.get("error"))
            investigation["report"] = report
        return investigation

    def wait_for_run(self, run_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
        with self._lock:
            control = self._controls.get(run_id)
        if control and control.thread:
            control.thread.join(timeout)
        return self.store.get_run(run_id)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.list_runs(limit)
