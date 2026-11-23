import tempfile
import unittest
import json
from pathlib import Path

from agentic_ops.detection import CategoryDetectorSuite, CountObservation
from agentic_ops.detector_host import DetectorHost, list_incidents
from agentic_ops.store import EventStore


def observation(index, count):
    return CountObservation(index, f"{index:03}", f"{index + 1:03}", {"payments": count})


class DetectorHostTests(unittest.TestCase):
    def test_restart_and_duplicate_delivery_preserve_results_and_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite")
            uninterrupted = DetectorHost(store, "continuous", CategoryDetectorSuite("poisson", 4, ["payments"]))
            restarted = DetectorHost(store, "restarted", CategoryDetectorSuite("poisson", 4, ["payments"]))
            counts = [4, 4, 4, 4, 40, 45] + [0] * 50 + [40]
            for index, count in enumerate(counts):
                expected = uninterrupted.process(observation(index, count))
                if index == 5:
                    restarted = DetectorHost(store, "restarted", CategoryDetectorSuite("poisson", 4, ["payments"]))
                actual = restarted.process(observation(index, count))
                self.assertEqual(expected, actual)
                self.assertEqual(actual, restarted.process(observation(index, count)))
            events = restarted.pending_events()
            transitions = [event for event in events if event["event_type"] != "episode_updated"]
            self.assertEqual([event["event_type"] for event in transitions],
                             ["episode_opened", "episode_ended", "episode_opened"])
            self.assertEqual(transitions[0]["episode_id"], transitions[1]["episode_id"])
            self.assertNotEqual(transitions[1]["episode_id"], transitions[2]["episode_id"])
            self.assertNotEqual(events[0]["episode_id"], uninterrupted.pending_events()[0]["episode_id"])
            self.assertEqual(restarted.deliver_pending(automatic=False), len(events))
            self.assertEqual(restarted.deliver_pending(automatic=False), 0)
            incidents = list_incidents(store, "restarted")
            self.assertEqual(len(incidents), 2)
            self.assertEqual({item["status"] for item in incidents}, {"active", "ended"})
            self.assertEqual({item["operational_status"] for item in incidents}, {"open"})

    def test_opened_episode_and_initial_job_are_delivered_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite")
            run_id = store.create_run("payment_issue", 1, "poisson", {})
            host = DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            for index, count in enumerate([4, 4, 4, 4, 40]):
                item = observation(index, count)
                host.process(item)
                if index == 4:
                    store.add_event(run_id, item.window_end, "detector", "anomaly_alert",
                                    {"category": "payments", "window_index": index,
                                     "window_end": item.window_end, "ticket_count": 40,
                                     "expected": 4.5})
            self.assertEqual(host.deliver_pending(automatic=True), 1)
            self.assertEqual(host.deliver_pending(automatic=True), 0)
            with store._connection() as db:
                incidents = db.execute("SELECT * FROM incidents WHERE stream_id=?", (run_id,)).fetchall()
                jobs = db.execute("SELECT * FROM incident_jobs WHERE stream_id=?", (run_id,)).fetchall()
            self.assertEqual(len(incidents), 1)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["reason"], "initial alert")
            self.assertEqual(jobs[0]["sequence_bound"], 1)
            listed = list_incidents(store, run_id)[0]
            self.assertEqual(listed["statistics"], {
                "alert_windows": 1, "tickets": 40, "expected": 4.5, "peak_window_tickets": 40,
            })
            self.assertEqual(listed["latest_investigation"]["status"], "queued")

    def test_missing_or_conflicting_observation_cannot_advance_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = DetectorHost(EventStore(Path(tmp) / "events.sqlite"), "stream",
                                CategoryDetectorSuite("poisson", 4, ["payments"]))
            host.process(observation(0, 4))
            with self.assertRaises(ValueError):
                host.process(observation(2, 4))
            with self.assertRaises(ValueError):
                host.process(observation(0, 5))
            with self.assertRaises(ValueError):
                host.process(CountObservation(1, "001", "002", {}))
            host.process(observation(1, 4))

    def test_automatic_episode_end_does_not_queue_final_job_and_redelivery_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite")
            run_id = store.create_run("payment_issue", 1, "poisson", {})
            host = DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            first_episode = None
            ended = False
            for index, count in enumerate([4, 4, 4, 4, 40, 45] + [0] * 50 + [40]):
                item = observation(index, count)
                result = host.process(item)
                for event in host.pending_events():
                    if event["event_type"] in {"episode_opened", "episode_updated"}:
                        store.add_event(run_id, item.window_end, "detector", "anomaly_alert",
                                        {"category": "payments", "window_index": index,
                                         "window_end": item.window_end})
                    if event["event_type"] == "episode_opened" and first_episode is None:
                        first_episode = event["episode_id"]
                    if event["event_type"] == "episode_ended" and event["episode_id"] == first_episode:
                        ended = True
                host.deliver_pending(automatic=True)
                self.assertIsNotNone(result)
            self.assertTrue(ended)
            incidents = list_incidents(store, run_id)
            first = next(item for item in incidents if item["incident_id"] == first_episode)
            self.assertEqual((first["status"], first["operational_status"]), ("ended", "open"))
            self.assertEqual(len(incidents), 2)
            with store._connection() as db:
                jobs = db.execute("SELECT * FROM incident_jobs WHERE stream_id=?", (run_id,)).fetchall()
                self.assertEqual([job["reason"] for job in jobs], ["initial alert", "initial alert"])
                old_event = db.execute("SELECT event_id FROM episode_outbox WHERE episode_id=? "
                                       "AND event_type='episode_updated' LIMIT 1", (first_episode,)).fetchone()
                self.assertIsNotNone(old_event)
                db.execute("UPDATE episode_outbox SET delivered_at=NULL WHERE event_id=?", (old_event["event_id"],))
            restarted = DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            self.assertEqual(restarted.deliver_pending(automatic=True), 1)
            after = next(item for item in list_incidents(store, run_id) if item["incident_id"] == first_episode)
            self.assertEqual(after["status"], "ended")
            self.assertEqual(after["last_event_id"], first["last_event_id"])
            self.assertEqual(after["episode"], first["episode"])

    def test_legacy_incident_migration_preserves_reports_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "legacy.sqlite")
            with store._connection() as db:
                db.executescript("""
                    CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL,
                        category TEXT NOT NULL, status TEXT NOT NULL, opened_at TEXT NOT NULL,
                        latest_window_end TEXT NOT NULL, last_event_id TEXT NOT NULL, episode_json TEXT NOT NULL);
                    CREATE TABLE incident_jobs (job_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
                        stream_id TEXT NOT NULL, reason TEXT NOT NULL, cutoff TEXT NOT NULL,
                        sequence_bound INTEGER NOT NULL, episode_json TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, report_json TEXT, error TEXT);
                """)
                db.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?)",
                           ("old", "stream", "payments", "ended", "001", "002", "event", "{}"))
                db.execute("INSERT INTO incident_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           ("job", "old", "stream", "initial alert", "002", 1, "{}", "completed",
                            "now", json.dumps({"summary": "saved"}), None))
            for _ in range(2):
                DetectorHost(store, "stream", CategoryDetectorSuite("poisson", 4, ["payments"]))
            incident = list_incidents(store, "stream")[0]
            self.assertEqual((incident["status"], incident["operational_status"]), ("ended", "open"))
            with store._connection() as db:
                self.assertEqual(json.loads(db.execute("SELECT report_json FROM incident_jobs WHERE job_id='job'").fetchone()[0]),
                                 {"summary": "saved"})

    def test_delivery_failure_rolls_back_incident_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite")
            run_id = store.create_run("payment_issue", 1, "poisson", {})
            host = DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            for index, count in enumerate([4, 4, 4, 4, 40]):
                host.process(observation(index, count))
            with self.assertRaisesRegex(ValueError, "alert evidence"):
                host.deliver_pending(automatic=True)
            self.assertEqual(list_incidents(store, run_id), [])
            self.assertEqual(len(host.pending_events()), 1)
            store.add_event(run_id, "005", "detector", "anomaly_alert",
                            {"category": "payments", "window_index": 4, "window_end": "005"})
            restarted = DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            self.assertEqual(restarted.deliver_pending(automatic=True), 1)
            self.assertEqual(len(list_incidents(store, run_id)), 1)

    def test_categories_keep_separate_incidents_and_manual_mode_has_no_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite")
            host = DetectorHost(store, "stream", CategoryDetectorSuite("poisson", 4, ["payments", "orders"]))
            for index, counts in enumerate([(4, 4)] * 4 + [(40, 4), (40, 40)] + [(0, 40)] * 5):
                host.process(CountObservation(index, f"{index:03}", f"{index + 1:03}",
                                              {"payments": counts[0], "orders": counts[1]}))
                host.deliver_pending(automatic=False)
            incidents = list_incidents(store, "stream")
            self.assertEqual({item["category"] for item in incidents}, {"payments", "orders"})
            self.assertEqual(len({item["incident_id"] for item in incidents}), len(incidents))
            with store._connection() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM incident_jobs").fetchone()[0], 0)

    def test_material_service_change_after_episode_ends_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite")
            run_id = store.create_run("payment_issue", 1, "poisson", {})
            host = DetectorHost(store, run_id, CategoryDetectorSuite("poisson", 4, ["payments"]))
            episode = '{"category":"payments","start_window":4,"latest_window":4}'
            with store._connection() as db:
                db.execute("INSERT INTO incidents (incident_id,stream_id,category,status,opened_at,latest_window_end,last_event_id,episode_json) "
                           "VALUES (?,?,?,?,?,?,?,?)", ("incident", run_id, "payments", "ended", "004", "005", "event", episode))
                db.execute("INSERT INTO incident_jobs (job_id,incident_id,stream_id,reason,cutoff,sequence_bound,episode_json,status,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)", ("job", "incident", run_id, "initial alert", "005", 1, episode, "completed", "now"))
            store.add_event(run_id, "005", "product_logs", "service_log_summary", {"service": "checkout-payments", "requests": 100, "errors": 1})
            store.add_event(run_id, "006", "product_logs", "service_log_summary", {"service": "checkout-payments", "requests": 100, "errors": 20})
            host.observe_completed_window(observation(6, 4), automatic=True)
            with store._connection() as db:
                pending = db.execute("SELECT * FROM pending_investigation_followups WHERE incident_id='incident'").fetchone()
                self.assertEqual(pending["cutoff"], "007")


if __name__ == "__main__":
    unittest.main()
