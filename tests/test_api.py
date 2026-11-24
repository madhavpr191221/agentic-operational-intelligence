import importlib.util
import tempfile
import unittest
from pathlib import Path

from agentic_ops.agent import MockInvestigator
from agentic_ops.service import OperationsRuntime
from agentic_ops.store import EventStore


@unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"),
                     "Install the optional API dependencies to run API tests.")
class ApiTests(unittest.TestCase):
    def test_health_and_scenario_catalog(self):
        from fastapi.testclient import TestClient
        from agentic_ops.api import create_app

        with tempfile.TemporaryDirectory() as directory:
            runtime = OperationsRuntime(EventStore(Path(directory) / "api.sqlite3"), MockInvestigator())
            client = TestClient(create_app(runtime))
            self.assertEqual(client.get("/api/health").json()["status"], "ok")
            self.assertEqual(client.get("/api/incidents").json(), [])
            self.assertEqual(client.get("/api/incidents/missing").status_code, 404)
            scenarios = client.get("/api/scenarios").json()
            self.assertEqual({item["id"] for item in scenarios},
                              {"mixed_categories", "payment_issue", "healthy", "unrelated_issue", "resolved_issue",
                              "orders_queue_backlog", "account_identity_outage",
                              "delivery_carrier_degradation"})
            runtime.stop()

    def test_incident_api_exposes_statistical_and_operational_state(self):
        from fastapi.testclient import TestClient
        from agentic_ops.api import create_app

        with tempfile.TemporaryDirectory() as directory:
            runtime = OperationsRuntime(EventStore(Path(directory) / "api.sqlite3"), MockInvestigator())
            try:
                with runtime.store._connection() as db:
                    db.execute("INSERT INTO incidents "
                               "(incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                               "VALUES (?,?,?,?,?,?,?,?)",
                               ("incident", "stream", "payments", "ended", "001", "002", "event", "{}"))
                    db.execute("INSERT INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,episode_json,status,created_at,report_json,error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               ("job", "incident", "stream", "initial alert", "002", 1, "{}", "completed",
                                "now", '{"summary":"saved"}', None))
                    db.execute("INSERT INTO incident_report_revisions VALUES (?,?,?)", ("incident", 1, "job"))
                client = TestClient(create_app(runtime))
                detail = client.get("/api/incidents/incident").json()
                self.assertEqual((detail["status"], detail["operational_status"]), ("ended", "open"))
                self.assertEqual(client.get("/api/incidents?stream_id=stream").json()[0]["incident_id"], "incident")
                self.assertEqual(client.get("/api/incidents/incident/reports/1").json()["report"]["summary"], "saved")
            finally:
                runtime.stop()

    def test_notification_inbox_and_acknowledgement_endpoints(self):
        from fastapi.testclient import TestClient
        from agentic_ops.api import create_app

        with tempfile.TemporaryDirectory() as directory:
            runtime = OperationsRuntime(EventStore(Path(directory) / "notifications.sqlite3"), MockInvestigator())
            try:
                run_id = runtime.store.create_run("payment_issue", 2, "poisson", {})
                runtime.store.add_event(run_id, "2026-01-01T10:10:00Z", "product_logs", "service_log_summary",
                                        {"service": "checkout-payments", "requests": 100, "errors": 20})
                runtime.store.add_event(run_id, "2026-01-01T10:30:00Z", "detector", "window_observation",
                                        {"window_index": 1, "window_start": "2026-01-01T10:00:00Z",
                                         "window_end": "2026-01-01T10:30:00Z"})
                with runtime.store._connection() as db:
                    db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json,operational_status) "
                               "VALUES (?,?,?,?,?,?,?,?,?)",
                               ("notification-incident", run_id, "payments", "active", "2026-01-01T10:30:00Z",
                                "2026-01-01T10:30:00Z", "event", '{"category":"payments","latest_window":1}', "open"))
                runtime.notifications.evaluate_incidents()
                client = TestClient(create_app(runtime))
                inbox = client.get(f"/api/notifications?stream_id={run_id}").json()
                self.assertEqual(inbox["email_mode"], "simulated")
                self.assertEqual(inbox["items"][0]["level"], "immediate")
                email = inbox["items"][0]["outbox"][0]
                self.assertEqual(email["draft"]["subject"], email["subject"])
                self.assertEqual(email["draft"]["body"], email["body"])
                self.assertEqual(email["draft"]["to"], email["recipient"])
                self.assertEqual(email["delivery_mode"], "simulated")
                response = client.post(f"/api/notifications/{inbox['items'][0]['notification_id']}/acknowledge")
                self.assertEqual(response.status_code, 200)
                self.assertIsNotNone(response.json()["acknowledged_at"])
            finally:
                runtime.stop()


if __name__ == "__main__":
    unittest.main()
