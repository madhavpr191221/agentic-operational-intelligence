import unittest

from agentic_ops.domain import SimulationConfig
from agentic_ops.simulation import generate_run


def event_signature(config: SimulationConfig):
    return [(window.start, window.end, window.ticket_count,
             [(event.simulated_at, event.source, event.event_type, event.payload) for event in window.events])
            for window in generate_run(config)]


class SimulationTests(unittest.TestCase):
    def test_same_seed_reproduces_event_stream(self):
        config = SimulationConfig(seed=190, window_count=60, calibration_windows=20)
        self.assertEqual(event_signature(config), event_signature(config))

    def test_different_seed_changes_random_arrivals(self):
        first = SimulationConfig(seed=8, window_count=60, calibration_windows=20)
        second = SimulationConfig(seed=9, window_count=60, calibration_windows=20)
        self.assertNotEqual([w.ticket_count for w in generate_run(first)],
                            [w.ticket_count for w in generate_run(second)])

    def test_payment_failure_raises_random_ticket_rate(self):
        config = SimulationConfig(seed=17)
        windows = list(generate_run(config))
        baseline = sum(w.ticket_count for w in windows[:48]) / 48
        incident = sum(w.ticket_count for w in windows[-4:]) / 4
        self.assertGreater(incident, baseline * 5)
        self.assertGreater(max(w.ticket_count for w in windows[-4:]), min(w.ticket_count for w in windows[-4:]))

    def test_issue_is_opened_48_hours_before_end(self):
        windows = list(generate_run(SimulationConfig(seed=1)))
        events = [event for window in windows for event in window.events
                  if event.source == "issue_tracker" and event.event_type == "issue_created"]
        self.assertEqual(len(events), 1)
        issue_at = next(i for i, window in enumerate(windows) if window.start == events[0].simulated_at)
        self.assertEqual((len(windows) - issue_at) * 30, 48 * 60)

    def test_unrelated_issue_does_not_change_payment_ticket_category(self):
        windows = list(generate_run(SimulationConfig(scenario="unrelated_issue", seed=5)))
        latest = [event for event in windows[-4] .events if event.source == "support_tickets"]
        self.assertTrue(latest)
        self.assertNotIn("payments", [event.payload["category"] for event in latest])


if __name__ == "__main__":
    unittest.main()
