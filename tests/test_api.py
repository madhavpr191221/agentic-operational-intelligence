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
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            scenarios = client.get("/api/scenarios").json()
            self.assertEqual({item["id"] for item in scenarios},
                             {"payment_issue", "healthy", "unrelated_issue", "resolved_issue"})


if __name__ == "__main__":
    unittest.main()
