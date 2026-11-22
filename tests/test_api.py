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
                             {"payment_issue", "healthy", "unrelated_issue", "resolved_issue",
                              "orders_queue_backlog", "delivery_carrier_degradation"})
            runtime.incident_worker.stop()

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
                runtime.incident_worker.stop()


if __name__ == "__main__":
    unittest.main()
