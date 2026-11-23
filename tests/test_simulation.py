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

    def test_count_only_generation_matches_full_run_counts_without_ticket_text(self):
        config = SimulationConfig(seed=91, window_count=60, calibration_windows=20)
        full = list(generate_run(config))
        counts_only = list(generate_run(config, include_ticket_events=False))
        self.assertEqual([w.category_counts for w in full], [w.category_counts for w in counts_only])
        self.assertEqual([w.ticket_count for w in full], [w.ticket_count for w in counts_only])
        self.assertTrue(all(not any(event.source == "support_tickets" for event in w.events)
                            for w in counts_only))

    def test_different_seed_changes_random_arrivals(self):
        first = SimulationConfig(seed=8, window_count=60, calibration_windows=20)
        second = SimulationConfig(seed=9, window_count=60, calibration_windows=20)
        self.assertNotEqual([w.ticket_count for w in generate_run(first)],
                            [w.ticket_count for w in generate_run(second)])

    def test_payment_failure_raises_payment_category_rate_to_about_31(self):
        config = SimulationConfig(seed=17)
        windows = list(generate_run(config))
        baseline = sum(w.category_counts["payments"] for w in windows[:48]) / 48
        incident = sum(w.category_counts["payments"] for w in windows[-4:]) / 4
        self.assertGreater(baseline, 3.5)
        self.assertLess(baseline, 6.5)
        self.assertGreater(incident, 24)
        self.assertLess(incident, 38)
        self.assertGreater(max(w.category_counts["payments"] for w in windows[-4:]),
                           min(w.category_counts["payments"] for w in windows[-4:]))

    def test_order_and_delivery_incidents_change_only_their_own_ticket_category(self):
        for scenario, category in (("orders_queue_backlog", "orders"),
                                   ("delivery_carrier_degradation", "delivery")):
            with self.subTest(scenario=scenario):
                windows = list(generate_run(SimulationConfig(scenario=scenario, seed=17)))
                baseline = sum(w.category_counts[category] for w in windows[:48]) / 48
                incident = sum(w.category_counts[category] for w in windows[-4:]) / 4
                self.assertGreater(incident, baseline * 2)
                for other in set(windows[0].category_counts) - {category}:
                    self.assertLess(sum(w.category_counts[other] for w in windows[-4:]) / 4,
                                    max(16, windows[0].category_counts[other] * 4))
                log_events = [event for window in windows[-4:] for event in window.events
                              if event.source == "product_logs"
                              and event.payload.get("service") == ("orders-api" if category == "orders" else "delivery-api")]
                self.assertEqual(len(log_events), 4)

    def test_issue_is_opened_48_hours_before_end(self):
        windows = list(generate_run(SimulationConfig(seed=1)))
        events = [event for window in windows for event in window.events
                  if event.source == "issue_tracker" and event.event_type == "issue_created"]
        self.assertEqual(len(events), 1)
        issue_at = next(i for i, window in enumerate(windows) if window.start == events[0].simulated_at)
        self.assertEqual((len(windows) - issue_at) * 30, 48 * 60)

    def test_unrelated_issue_does_not_change_payment_ticket_category(self):
        windows = list(generate_run(SimulationConfig(scenario="unrelated_issue", seed=5)))
        baseline = sum(w.category_counts["payments"] for w in windows[:48]) / 48
        distractor = [event for event in windows[-5].events
                      if event.source == "change_log" and event.payload.get("change_key") == "CHG-889"]
        incident_window = sum(w.category_counts["payments"] for w in windows[-4:]) / 4
        self.assertTrue(distractor)
        self.assertLess(incident_window, baseline * 2.5)

    def test_each_ticket_is_one_message_record_and_counts_reconcile(self):
        windows = list(generate_run(SimulationConfig(seed=31, window_count=12, calibration_windows=4)))
        for window in windows:
            tickets = [event for event in window.events if event.source == "support_tickets"]
            self.assertEqual(len(tickets), window.ticket_count)
            self.assertEqual(sum(window.category_counts.values()), window.ticket_count)
            self.assertTrue(all(event.payload.get("body") for event in tickets))
            self.assertTrue(all(event.payload.get("reason") for event in tickets))
            self.assertEqual({category: sum(event.payload["category"] == category for event in tickets)
                              for category in window.category_counts}, window.category_counts)

    def test_incident_wording_does_not_change_counts_or_ticket_record_count(self):
        for scenario in ("payment_issue", "orders_queue_backlog", "account_identity_outage",
                         "delivery_carrier_degradation"):
            config = SimulationConfig(scenario=scenario, seed=93, window_count=16, calibration_windows=4)
            full = list(generate_run(config))
            counts = list(generate_run(config, include_ticket_events=False))
            self.assertEqual([w.category_counts for w in full], [w.category_counts for w in counts])
            for window in full:
                tickets = [event for event in window.events if event.source == "support_tickets"]
                self.assertEqual(len(tickets), window.ticket_count)


if __name__ == "__main__":
    unittest.main()
