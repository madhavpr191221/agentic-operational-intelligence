"""Local FastAPI surface for simulation, event streaming, and investigation."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from agentic_ops.domain import SimulationConfig
from agentic_ops.detector_host import list_incidents
from agentic_ops.incident_worker import schedule_job
from agentic_ops.service import OperationsRuntime
from agentic_ops.simulation import LIVE_REPLAY_PROFILES, choose_live_replay_profile

Speed = Literal["slow", "normal", "fast"]
InvestigationMode = Literal["automatic", "manual"]
AlertLevel = Literal["normal", "medium", "high"]
ALERT_LEVEL_SEVERITY = {"normal": "none", "medium": "mild", "high": "severe"}
LiveScenario = Literal[
    "payments_normal", "payments_medium", "payments_high",
    "orders_high", "account_high", "delivery_high",
]


class RunRequest(BaseModel):
    scenario: Literal["payment_issue", "healthy", "unrelated_issue", "resolved_issue",
                      "orders_queue_backlog", "account_identity_outage",
                      "delivery_carrier_degradation"] = "payment_issue"
    seed: int | None = None
    arrival_model: Literal["poisson", "negative_binomial"] = "poisson"
    speed: Speed = "normal"
    investigation_mode: InvestigationMode = "automatic"
    randomized_week: bool = False
    alert_level: AlertLevel | None = None
    live_scenario: LiveScenario | None = None


class EpisodeSelection(BaseModel):
    category: str
    start_window_index: int = Field(ge=0)
    end_window_index: int = Field(ge=0)
    focus_window_index: int = Field(ge=0)


class InvestigationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    alert_evidence_id: str | None = None
    alert_evidence_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)
    episode_selections: list[EpisodeSelection] | None = Field(default=None, min_length=1, max_length=4)


class IncidentInvestigationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=200)


class IncidentActionRequest(BaseModel):
    action_description: str = Field(min_length=1, max_length=2000)
    occurred_at: datetime
    performed_by: str | None = Field(default=None, max_length=200)
    reference: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=200)


class IncidentClosureRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    supporting_context: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class IncidentReopenRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)


def create_app(runtime: OperationsRuntime | None = None) -> FastAPI:
    selected_runtime = runtime or OperationsRuntime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            selected_runtime.stop()

    app = FastAPI(title="Agentic Operational Intelligence", version="0.1.0", lifespan=lifespan)
    app.state.runtime = selected_runtime

    @app.get("/api/health")
    def health() -> dict[str, str | bool]:
        mode = app.state.runtime.investigator_mode
        return {"status": "ok", "investigator_mode": mode,
                "openai_key_configured": app.state.runtime.openai_key_configured}

    @app.get("/api/incidents")
    def incidents(stream_id: str | None = None) -> list[dict]:
        return list_incidents(app.state.runtime.store, stream_id)

    @app.get("/api/notifications")
    def notifications(stream_id: str | None = None) -> dict:
        coordinator = app.state.runtime.notifications
        return {"email_mode": coordinator.adapter.mode,
                "acknowledgement_timeout_seconds": coordinator.settings.acknowledgement_timeout_seconds,
                "items": coordinator.list_assessments(stream_id)}

    @app.post("/api/notifications/{notification_id}/acknowledge")
    def acknowledge_notification(notification_id: str) -> dict:
        try:
            return app.state.runtime.notifications.acknowledge(notification_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/investigation-queue")
    def investigation_queue() -> dict:
        with app.state.runtime.store._connection() as db:
            rows = db.execute("SELECT COALESCE(queued_at,created_at) AS queued_at FROM incident_jobs WHERE status='queued' ORDER BY queue_order,rowid").fetchall()
            running = db.execute("SELECT COUNT(*) FROM incident_jobs WHERE status='running'").fetchone()[0]
            pending = db.execute("SELECT COUNT(*) FROM pending_investigation_followups").fetchone()[0]
        oldest = min((datetime.fromisoformat(row["queued_at"].replace("Z", "+00:00")) for row in rows), default=None)
        return {"queued": len(rows), "running": running, "pending_followups": pending,
                "oldest_wait_seconds": max(0, int((datetime.now(timezone.utc) - oldest).total_seconds())) if oldest else None}

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str) -> dict:
        try:
            return app.state.runtime.incident_workspace.detail(incident_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/actions", status_code=201)
    def record_incident_action(incident_id: str, request: IncidentActionRequest) -> dict:
        try:
            return app.state.runtime.incident_workspace.record_action(
                incident_id, description=request.action_description, occurred_at=request.occurred_at,
                performed_by=request.performed_by, reference=request.reference,
                idempotency_key=request.idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/close", status_code=201)
    def close_incident(incident_id: str, request: IncidentClosureRequest) -> dict:
        try:
            return app.state.runtime.incident_workspace.close(
                incident_id, reason=request.reason, context=request.supporting_context,
                idempotency_key=request.idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/reopen", status_code=201)
    def reopen_incident(incident_id: str, request: IncidentReopenRequest) -> dict:
        try:
            return app.state.runtime.incident_workspace.reopen(
                incident_id, reason=request.reason, idempotency_key=request.idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/investigations", status_code=202)
    def investigate_incident(incident_id: str, request: IncidentInvestigationRequest) -> dict:
        try:
            return schedule_job(app.state.runtime.store, incident_id, request.reason, request.idempotency_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def incident_job(job_id: str) -> dict:
        with app.state.runtime.store._connection() as db:
            row = db.execute("SELECT * FROM incident_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        result = dict(row)
        result["report"] = json.loads(result.pop("report_json")) if result["report_json"] else None
        return result

    @app.get("/api/incidents/{incident_id}/reports/{revision}")
    def incident_report(incident_id: str, revision: int) -> dict:
        if revision < 1:
            raise HTTPException(status_code=404, detail="Unknown report")
        with app.state.runtime.store._connection() as db:
            row = db.execute("SELECT j.job_id,j.cutoff,j.report_json FROM incident_report_revisions r "
                             "JOIN incident_jobs j ON j.job_id=r.job_id "
                             "WHERE r.incident_id=? AND r.revision=?", (incident_id, revision)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown report")
        return {"incident_id": incident_id, "revision": revision, "job_id": row["job_id"],
                "cutoff": row["cutoff"], "report": json.loads(row["report_json"])}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        with app.state.runtime.store._connection() as db:
            updated = db.execute("UPDATE incident_jobs SET status='cancelled' WHERE job_id=? AND status='queued'",
                                 (job_id,))
        if not updated.rowcount:
            raise HTTPException(status_code=409, detail="Job is not queued")
        return {"job_id": job_id, "status": "cancelled"}

    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, str]]:
        return [
            {"id": "payment_issue", "label": "Unresolved payment failure"},
            {"id": "healthy", "label": "Healthy control"},
            {"id": "unrelated_issue", "label": "Unrelated issue distractor"},
            {"id": "resolved_issue", "label": "Resolved payment issue"},
            {"id": "orders_queue_backlog", "label": "Order processing backlog"},
            {"id": "account_identity_outage", "label": "Account sign-in and identity failures"},
            {"id": "delivery_carrier_degradation", "label": "Carrier service degradation"},
        ]

    @app.get("/api/runs")
    def list_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
        return app.state.runtime.list_runs(limit)

    @app.post("/api/runs", status_code=202)
    def start_run(request: RunRequest) -> dict:
        if request.alert_level is not None and not request.randomized_week:
            raise HTTPException(status_code=422, detail="alert_level requires a seeded live replay")
        if request.live_scenario is not None and not request.randomized_week:
            raise HTTPException(status_code=422, detail="live_scenario requires a seeded live replay")
        if request.live_scenario is not None and request.alert_level is not None:
            raise HTTPException(status_code=422, detail="Choose either live_scenario or alert_level")
        if (request.alert_level is not None or request.live_scenario is not None) and not app.state.runtime.openai_key_configured:
            raise HTTPException(status_code=409, detail="The seeded demo requires an OpenAI investigator and configured key")
        if request.randomized_week:
            if request.seed is None:
                severity = ALERT_LEVEL_SEVERITY.get(request.alert_level) if request.alert_level else None
                profile = (choose_live_replay_profile(live_scenario=request.live_scenario)
                           if request.live_scenario is not None
                           else choose_live_replay_profile(severity))
            else:
                profile = next((item for item in LIVE_REPLAY_PROFILES if item.seed == request.seed), None)
                if profile is None:
                    raise HTTPException(status_code=422, detail="Seed is not in the live replay seed pool")
                if request.live_scenario is None and not profile.live_scenario.startswith("payments_"):
                    raise HTTPException(status_code=422, detail="Select the matching live_scenario for this seed")
                expected_severity = ALERT_LEVEL_SEVERITY.get(request.alert_level) if request.alert_level else None
                if expected_severity is not None and profile.severity != expected_severity:
                    raise HTTPException(status_code=422, detail="Seed does not match the selected alert level")
                if request.live_scenario is not None and profile.live_scenario != request.live_scenario:
                    raise HTTPException(status_code=422, detail="Seed does not match the selected demo scenario")
            config = SimulationConfig(
                scenario=profile.scenario,
                seed=profile.seed,
                arrival_model=request.arrival_model,
                tick_delay_seconds=0.0,
                window_count=336,
                calibration_windows=192,
                monitor_history_windows=192,
                playback_duration_seconds=120.0,
                incident_start_window=profile.incident_start_window,
                incident_duration_windows=4,
                incident_severity=profile.severity,
            )
        else:
            delay = {"slow": 0.45, "normal": 0.12, "fast": 0.025}[request.speed]
            config = SimulationConfig(scenario=request.scenario, seed=request.seed if request.seed is not None else 42,
                                      arrival_model=request.arrival_model, tick_delay_seconds=delay)
        return app.state.runtime.start_run(config, request.investigation_mode)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        result = app.state.runtime.store.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Unknown run")
        return result

    @app.post("/api/runs/{run_id}/controls/{action}")
    def control_run(run_id: str, action: Literal["pause", "resume", "step", "stop"]) -> dict:
        try:
            return app.state.runtime.control_run(run_id, action)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, after_sequence: int = Query(default=0, ge=0),
               limit: int = Query(default=500, ge=1, le=2000)) -> list[dict]:
        if app.state.runtime.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Unknown run")
        return app.state.runtime.store.list_events(run_id, after_sequence, limit)

    @app.get("/api/runs/{run_id}/stream")
    async def stream_events(run_id: str, request: Request,
                            after_sequence: int = Query(default=0, ge=0)) -> StreamingResponse:
        if app.state.runtime.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Unknown run")

        async def event_generator() -> AsyncIterator[str]:
            try:
                last_event_id = int(request.headers.get("last-event-id", "0"))
            except ValueError:
                last_event_id = 0
            cursor = max(after_sequence, last_event_id)
            while True:
                batch = app.state.runtime.store.list_events(run_id, cursor, 200)
                for event in batch:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                run = app.state.runtime.store.get_run(run_id)
                if run is None or (run["status"] in {"completed", "failed", "stopped"} and not batch):
                    break
                if not batch:
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(0.25)

        return StreamingResponse(event_generator(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/alerts")
    def list_alerts(run_id: str) -> list[dict]:
        if app.state.runtime.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Unknown run")
        return app.state.runtime.store.search_events(run_id, ("detector",), limit=1000,
                                                     event_types=("anomaly_alert",))

    @app.post("/api/runs/{run_id}/investigations", status_code=202)
    def investigate(run_id: str, request: InvestigationRequest) -> dict:
        try:
            return app.state.runtime.start_investigation(
                run_id, request.question, request.alert_evidence_id, request.alert_evidence_ids,
                [selection.model_dump() for selection in request.episode_selections]
                if request.episode_selections else None,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/investigations/{investigation_id}")
    def get_investigation(run_id: str, investigation_id: str) -> dict:
        result = app.state.runtime.get_investigation(investigation_id)
        if result is None or result["run_id"] != run_id:
            raise HTTPException(status_code=404, detail="Unknown investigation")
        return result

    return app


app = create_app()
