"""Local FastAPI surface for simulation, event streaming, and investigation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from agentic_ops.domain import SimulationConfig
from agentic_ops.service import OperationsRuntime

Speed = Literal["slow", "normal", "fast"]


class RunRequest(BaseModel):
    scenario: Literal["payment_issue", "healthy", "unrelated_issue", "resolved_issue"] = "payment_issue"
    seed: int = 42
    arrival_model: Literal["poisson", "negative_binomial"] = "poisson"
    speed: Speed = "normal"


class InvestigationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    alert_evidence_id: str | None = None
    alert_evidence_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)


def create_app(runtime: OperationsRuntime | None = None) -> FastAPI:
    app = FastAPI(title="Agentic Operational Intelligence", version="0.1.0")
    app.state.runtime = runtime or OperationsRuntime()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, str]]:
        return [
            {"id": "payment_issue", "label": "Unresolved payment failure"},
            {"id": "healthy", "label": "Healthy control"},
            {"id": "unrelated_issue", "label": "Unrelated issue distractor"},
            {"id": "resolved_issue", "label": "Resolved payment issue"},
        ]

    @app.get("/api/runs")
    def list_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
        return app.state.runtime.list_runs(limit)

    @app.post("/api/runs", status_code=202)
    def start_run(request: RunRequest) -> dict:
        delay = {"slow": 0.45, "normal": 0.12, "fast": 0.025}[request.speed]
        config = SimulationConfig(scenario=request.scenario, seed=request.seed,
                                  arrival_model=request.arrival_model, tick_delay_seconds=delay)
        return app.state.runtime.start_run(config)

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
                run_id, request.question, request.alert_evidence_id, request.alert_evidence_ids
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/investigations/{investigation_id}")
    def get_investigation(run_id: str, investigation_id: str) -> dict:
        result = app.state.runtime.store.get_investigation(investigation_id)
        if result is None or result["run_id"] != run_id:
            raise HTTPException(status_code=404, detail="Unknown investigation")
        return result

    return app


app = create_app()
