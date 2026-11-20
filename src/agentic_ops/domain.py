"""Shared domain types for simulation, detection, evidence, and RCA."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ScenarioName = Literal["payment_issue", "healthy", "unrelated_issue", "resolved_issue"]
ArrivalModel = Literal["poisson", "negative_binomial"]
CATEGORIES = ("payments", "orders", "account", "delivery")


@dataclass(frozen=True)
class SimulationConfig:
    scenario: ScenarioName = "payment_issue"
    seed: int = 42
    arrival_model: ArrivalModel = "poisson"
    tick_delay_seconds: float = 0.15
    window_count: int = 144
    calibration_windows: int = 48
    baseline_tickets_per_window: float = 3.5
    payment_attempts_per_window: float = 120.0
    contact_probability_per_failure: float = 0.85
    negative_binomial_shape: float = 4.0


@dataclass
class TelemetryEvent:
    event_id: str
    run_id: str
    simulated_at: str
    source: str
    event_type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Score:
    detector: str
    score_name: str
    score: float
    threshold: float
    expected: float
    observed: int
    anomalous: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceRef:
    evidence_id: str
    source: str
    observed_at: str
    summary: str
    window_index: int | None = None


@dataclass
class Hypothesis:
    title: str
    explanation: str
    rank: int
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


@dataclass
class CategoryFinding:
    category: str
    status: Literal["completed", "abstained", "incomplete"]
    summary: str
    unresolved_questions: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    category: str
    runbook_id: str | None
    title: str
    next_step: str
    verification: str
    supporting_evidence: list[str] = field(default_factory=list)


@dataclass
class RCAReport:
    status: Literal["completed", "abstained", "incomplete", "failed"]
    summary: str
    hypotheses: list[Hypothesis]
    evidence: list[EvidenceRef]
    unresolved_questions: list[str] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    category_findings: list[CategoryFinding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    episode_summaries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
