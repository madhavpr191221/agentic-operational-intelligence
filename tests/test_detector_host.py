import tempfile
import unittest
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
                                     "window_end": item.window_end})
            self.assertEqual(host.deliver_pending(automatic=True), 1)
            self.assertEqual(host.deliver_pending(automatic=True), 0)
            with store._connection() as db:
                incidents = db.execute("SELECT * FROM incidents WHERE stream_id=?", (run_id,)).fetchall()
                jobs = db.execute("SELECT * FROM incident_jobs WHERE stream_id=?", (run_id,)).fetchall()
            self.assertEqual(len(incidents), 1)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["reason"], "initial alert")
            self.assertEqual(jobs[0]["sequence_bound"], 1)

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


if __name__ == "__main__":
    unittest.main()
