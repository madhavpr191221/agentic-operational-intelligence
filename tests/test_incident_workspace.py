import tempfile
import unittest
from pathlib import Path
from datetime import datetime

from agentic_ops.agent import MockInvestigator
from agentic_ops.service import OperationsRuntime
from agentic_ops.store import EventStore
from agentic_ops.api import create_app
from fastapi.testclient import TestClient


class IncidentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = OperationsRuntime(
            EventStore(Path(self.temp.name) / "workspace.sqlite3"), MockInvestigator(),
        )
        self.run_id = self.runtime.store.create_run("payment_issue", 1, "poisson", {})
        with self.runtime.store._connection() as db:
            db.execute(
                "INSERT INTO incidents(incident_id,stream_id,category,status,opened_at,latest_window_end,"
                "last_event_id,episode_json,operational_status) VALUES(?,?,?,?,?,?,?,?,?)",
                ("incident", self.run_id, "payments", "ended", "2026-01-01T10:00:00Z",
                 "2026-01-01T11:00:00Z", "event", '{"category":"payments","start_window":0,'
                 '"latest_window":1,"window_count":1,"peak_ticket_count":20}', "open"),
            )
            db.execute(
                "INSERT INTO detector_host_observations(stream_id,window_index,observation_json,result_json) "
                "VALUES(?,?,?,?)",
                (self.run_id, 2,
                 '{"window_start":"2026-01-01T11:00:00Z","window_end":"2026-01-01T11:30:00Z",'
                 '"category_counts":{"payments":3}}',
                 '{"categories":{"payments":{"expected":4.5}}}'),
            )
        self.runtime.store.add_event(self.run_id, "2026-01-01T10:30:00Z", "detector", "anomaly_alert",
                                     {"category": "payments", "window_index": 0,
                                      "ticket_count": 20, "expected": 4.5})
        self.runtime.store.add_event(self.run_id, "2026-01-01T11:20:00Z", "product_logs",
                                     "service_log_summary", {"service": "checkout-payments",
                                     "level": "info", "message": "Error rate within normal range.",
                                     "errors": 2, "requests": 200})

    def tearDown(self):
        self.runtime.stop()
        self.temp.cleanup()

    def test_action_is_attributed_idempotent_and_separate_from_closure(self):
        workspace = self.runtime.incident_workspace
        args = {
            "description": "Engineering reported disabling the new payment route",
            "occurred_at": datetime.fromisoformat("2026-01-01T11:10:00+00:00"),
            "performed_by": "Payments engineering",
            "reference": "OPS-123",
            "idempotency_key": "action-1",
        }
        first = workspace.record_action("incident", **args)
        second = workspace.record_action("incident", **args)
        self.assertEqual(first["entry_id"], second["entry_id"])
        self.assertEqual(first["recorded_by"], "Local operator")
        self.assertEqual(first["payload"]["source_type"], "operator_reported")
        self.assertEqual(first["payload"]["performed_by"], "Payments engineering")
        self.assertEqual(workspace.detail("incident")["operational_status"], "open")

    def test_detail_includes_episode_totals_and_post_episode_observations(self):
        detail = self.runtime.incident_workspace.detail("incident")
        self.assertEqual(detail["statistics"]["alert_windows"], 1)
        self.assertEqual(detail["statistics"]["tickets"], 20)
        self.assertEqual(detail["statistics"]["expected"], 4.5)
        self.assertEqual(detail["monitoring"]["ticket_windows"][0]["tickets"], 3)
        self.assertEqual(detail["monitoring"]["service_health"][0]["service"], "checkout-payments")

    def test_close_reopen_are_human_timeline_transitions(self):
        workspace = self.runtime.incident_workspace
        closed = workspace.close("incident", reason="Engineering confirmed payment processing is restored",
                                 context="Gateway error rate returned to normal.", idempotency_key="close-1")
        self.assertEqual(closed["event_type"], "incident_closed")
        self.assertEqual(workspace.detail("incident")["operational_status"], "resolved")
        with self.assertRaisesRegex(ValueError, "Reopen the incident"):
            workspace.record_action("incident", description="Late action report",
                                    occurred_at=datetime.fromisoformat(
                                        "2026-01-01T11:15:00+00:00"), idempotency_key="action-late")
        reopened = workspace.reopen("incident", reason="New customer failures appeared",
                                    idempotency_key="reopen-1")
        self.assertEqual(reopened["event_type"], "incident_reopened")
        detail = workspace.detail("incident")
        self.assertEqual(detail["operational_status"], "open")
        self.assertEqual([entry["event_type"] for entry in detail["lifecycle_history"]],
                         ["incident_closed", "incident_reopened"])

    def test_idempotency_key_cannot_change_meaning(self):
        workspace = self.runtime.incident_workspace
        occurred = datetime.fromisoformat("2026-01-01T11:10:00+00:00")
        workspace.record_action("incident", description="Engineering restarted the gateway",
                                 occurred_at=occurred, idempotency_key="same-key")
        with self.assertRaisesRegex(ValueError, "already used"):
            workspace.record_action("incident", description="Engineering rolled back the gateway",
                                    occurred_at=occurred, idempotency_key="same-key")

    def test_api_action_close_and_reopen_workflow(self):
        client = TestClient(create_app(self.runtime))
        action = {
            "action_description": "Engineering reported a configuration rollback",
            "occurred_at": "2026-01-01T11:10:00Z",
            "performed_by": "Payments engineering",
            "reference": "OPS-123",
            "idempotency_key": "api-action",
        }
        first = client.post("/api/incidents/incident/actions", json=action)
        repeated = client.post("/api/incidents/incident/actions", json=action)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["entry_id"], repeated.json()["entry_id"])
        detail = client.get("/api/incidents/incident").json()
        self.assertEqual(detail["actions"][0]["payload"]["source_type"], "operator_reported")
        self.assertEqual(detail["operator_name"], "Local operator")

        closed = client.post("/api/incidents/incident/close", json={
            "reason": "Engineering confirmed service recovery", "supporting_context": "Errors are normal.",
            "idempotency_key": "api-close",
        })
        self.assertEqual(closed.status_code, 201)
        self.assertEqual(client.get("/api/incidents/incident").json()["operational_status"], "resolved")
        blocked = client.post("/api/incidents/incident/actions", json={**action, "idempotency_key": "late-action"})
        self.assertEqual(blocked.status_code, 409)
        reopened = client.post("/api/incidents/incident/reopen", json={
            "reason": "New failures appeared", "idempotency_key": "api-reopen",
        })
        self.assertEqual(reopened.status_code, 201)
        detail = client.get("/api/incidents/incident").json()
        self.assertEqual(detail["operational_status"], "open")
        self.assertEqual(len(detail["lifecycle_history"]), 2)


if __name__ == "__main__":
    unittest.main()
