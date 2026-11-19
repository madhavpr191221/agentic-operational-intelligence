"""Run orchestration and investigation jobs."""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from agentic_ops.agent import Investigator, MockInvestigator, OpenAIInvestigator
from agentic_ops.detectors import CountDetectorSuite, calibrated_config
from agentic_ops.domain import SimulationConfig
from agentic_ops.evidence import EvidenceTools
from agentic_ops.simulation import generate_run
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
    thread: threading.Thread | None = None


class OperationsRuntime:
    def __init__(self, store: EventStore | None = None, investigator: Investigator | None = None):
        self.store = store or EventStore()
        self.evidence = EvidenceTools(self.store)
        self._provided_investigator = investigator
        self._controls: dict[str, RunControl] = {}
        self._lock = threading.Lock()

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

    def start_run(self, config: SimulationConfig) -> dict[str, Any]:
        run_id = self.store.create_run(config.scenario, config.seed, config.arrival_model, asdict(config))
        control = RunControl()
        with self._lock:
            self._controls[run_id] = control
        truth = {"scenario": config.scenario,
                 "cause": "payment_authorization_failure" if config.scenario == "payment_issue" else
                         ("unrelated_search_issue" if config.scenario == "unrelated_issue" else "none"),
                 "issue_opens_at_window": min(config.calibration_windows, config.window_count // 3),
                 "severe_failure_starts_at_window": config.window_count - 4}
        self.store.add_truth(run_id, truth)
        self.store.set_run_status(run_id, "running")
        start = "2026-01-01T00:00:00Z"
        self.store.add_event(run_id, start, "system", "run_started",
                             {"scenario": config.scenario, "seed": config.seed, "arrival_model": config.arrival_model,
                              "window_count": config.window_count})
        thread = threading.Thread(target=self._run_simulation, args=(run_id, config, control), daemon=True,
                                  name=f"simulation-{run_id[:8]}")
        control.thread = thread
        thread.start()
        return self.store.get_run(run_id) or {"run_id": run_id}

    def _run_simulation(self, run_id: str, config: SimulationConfig, control: RunControl) -> None:
        suite = CountDetectorSuite(calibrated_config(config.arrival_model, config.calibration_windows))
        try:
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
                observation = self.store.add_event(
                    run_id, window.end, "detector", "window_observation",
                    {"window_index": window.index, "window_start": window.start, "window_end": window.end,
                     "ticket_count": window.ticket_count},
                )
                scores, alerted = suite.update(window.ticket_count, window.index)
                self.store.add_event(run_id, window.end, "detector", "window_score",
                                     {"window_index": window.index, "observed": window.ticket_count,
                                      "expected": suite.expected, "scores": [score.to_dict() for score in scores],
                                      "calibration": window.index < config.calibration_windows,
                                      "observation_evidence_id": observation["evidence_id"]})
                if alerted:
                    triggered = [score.detector for score in scores if score.anomalous]
                    self.store.add_event(run_id, window.end, "detector", "anomaly_alert",
                                         {"window_index": window.index, "window_start": window.start,
                                          "window_end": window.end, "ticket_count": window.ticket_count,
                                          "detectors_triggered": triggered,
                                          "observation_evidence_id": observation["evidence_id"]})
                if config.tick_delay_seconds > 0:
                    time.sleep(config.tick_delay_seconds)
            self.store.set_run_status(run_id, "completed")
        except Exception as exc:  # keep a visible error state for the local operator
            self.store.set_run_status(run_id, "failed", str(exc))

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
                control.paused = True
                self.store.set_run_status(run_id, "paused")
            elif action == "resume":
                control.paused = False
                self.store.set_run_status(run_id, "running")
                control.condition.notify_all()
            elif action == "step":
                control.paused = True
                control.step_budget += 1
                self.store.set_run_status(run_id, "paused")
                control.condition.notify_all()
            elif action == "stop":
                control.stopped = True
                control.paused = False
                self.store.set_run_status(run_id, "stopping")
                control.condition.notify_all()
            else:
                raise ValueError("Action must be pause, resume, step, or stop")
        return {"run_id": run_id, "action": action, "status": (self.store.get_run(run_id) or {}).get("status")}

    def start_investigation(self, run_id: str, question: str,
                            alert_evidence_id: str | None = None) -> dict[str, Any]:
        if self.store.get_run(run_id) is None:
            raise KeyError("Unknown run")
        alerts = self.store.search_events(run_id, ("detector",), limit=200,
                                          event_types=("anomaly_alert",))
        if alert_evidence_id:
            alert = next((event for event in alerts if event["evidence_id"] == alert_evidence_id), None)
            if alert is None:
                raise ValueError("Selected alert does not belong to this run")
        else:
            alert = alerts[-1] if alerts else None
        investigation_id = self.store.create_investigation(run_id, question)
        context = ({"evidence_id": alert["evidence_id"], "simulated_at": alert["simulated_at"],
                    **alert["payload"]} if alert else None)
        worker = threading.Thread(target=self._investigate,
                                  args=(investigation_id, run_id, question, context), daemon=True,
                                  name=f"investigation-{investigation_id[:8]}")
        worker.start()
        return self.store.get_investigation(investigation_id) or {"investigation_id": investigation_id}

    def _investigate(self, investigation_id: str, run_id: str, question: str,
                     alert_context: dict[str, Any] | None) -> None:
        self.store.set_investigation(investigation_id, "running")
        try:
            report = self._investigator().investigate(run_id, question, self.evidence, alert_context)
            self.store.set_investigation(investigation_id, report.status, report.to_dict())
        except Exception as exc:
            self.store.set_investigation(investigation_id, "failed", error=str(exc))

    def wait_for_run(self, run_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
        with self._lock:
            control = self._controls.get(run_id)
        if control and control.thread:
            control.thread.join(timeout)
        return self.store.get_run(run_id)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.list_runs(limit)
