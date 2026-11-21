from __future__ import annotations

from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from agentic_ops.api import create_app
from agentic_ops.detection import CategoryDetectorSuite, CountObservation
from agentic_ops.domain import CATEGORIES, SimulationConfig
from agentic_ops.simulation import LIVE_REPLAY_PROFILES, generate_live_week


class FakeRuntime:
    investigator_mode = "openai"
    openai_key_configured = True

    def __init__(self):
        self.start_run = Mock(side_effect=lambda config, mode: {
            "seed": config.seed, "config": {**config.__dict__, "investigation_mode": mode},
        })


def test_health_reports_key_presence_without_returning_secret():
    runtime = FakeRuntime()
    response = TestClient(create_app(runtime)).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "investigator_mode": "openai", "openai_key_configured": True,
    }


def test_alert_level_selects_a_matching_profile():
    runtime = FakeRuntime()
    app = create_app(runtime)
    for level, severity in (("normal", "none"), ("medium", "mild"), ("high", "severe")):
        profile = next(item for item in LIVE_REPLAY_PROFILES if item.severity == severity)
        with patch("agentic_ops.api.choose_live_replay_profile", return_value=profile) as choose:
            response = TestClient(app).post("/api/runs", json={
                "randomized_week": True, "alert_level": level,
            })
        assert response.status_code == 202
        choose.assert_called_once_with(severity)
        config = runtime.start_run.call_args.args[0]
        assert config.seed == profile.seed
        assert config.incident_severity == severity
        assert config.incident_start_window == (None if severity == "none" else 216)
        assert runtime.start_run.call_args.args[1] == "automatic"


def test_demo_does_not_start_without_openai_key():
    runtime = FakeRuntime()
    runtime.openai_key_configured = False
    response = TestClient(create_app(runtime)).post("/api/runs", json={
        "randomized_week": True, "alert_level": "high",
    })
    assert response.status_code == 409
    assert "requires an OpenAI investigator" in response.json()["detail"]
    runtime.start_run.assert_not_called()


def test_seed_must_match_selected_alert_level():
    runtime = FakeRuntime()
    response = TestClient(create_app(runtime)).post("/api/runs", json={
        "randomized_week": True, "alert_level": "high", "seed": 2201,
    })
    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    runtime.start_run.assert_not_called()


def test_incident_profiles_alert_within_their_four_early_windows():
    incident_profiles = [profile for profile in LIVE_REPLAY_PROFILES if profile.severity in {"mild", "severe"}]
    for profile in incident_profiles:
        config = SimulationConfig(
            scenario="payment_issue", seed=profile.seed, window_count=336,
            calibration_windows=192, monitor_history_windows=192,
            playback_duration_seconds=120.0, incident_start_window=profile.incident_start_window,
            incident_duration_windows=4, incident_severity=profile.severity,
        )
        detector = CategoryDetectorSuite("poisson", 192, CATEGORIES)
        alerted_windows = []
        for window in generate_live_week(config):
            result = detector.update(CountObservation(
                window.index, window.start, window.end, window.category_counts,
            ))
            if window.index >= 192 and result.categories["payments"].alerted:
                alerted_windows.append(window.index)
        assert profile.incident_start_window == 216
        assert any(216 <= index <= 219 for index in alerted_windows), (
            f"Seed {profile.seed} ({profile.severity}) did not alert during the planted spike"
        )
