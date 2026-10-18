"""Unit tests for food rescue matching in agents/followup.py.  Run:  python -m unittest discover tests"""

import unittest
from datetime import datetime

from agents.followup import LA, estimate, load_orgs, match_orgs
from shared.models import LeftoverReport

WESTWOOD = (34.0689, -118.4452)
TUESDAY_NOON = datetime(2026, 10, 20, 12, 0, tzinfo=LA)
SUNDAY_NIGHT = datetime(2026, 10, 18, 22, 0, tzinfo=LA)


def report(**kw) -> LeftoverReport:
    fields = {"amount": "tray", "food_type": "hot", "kept_safe": "yes", "handoff": "dropoff"} | kw
    return LeftoverReport(rfq_id="rfq_test", **fields)


class MatchTests(unittest.TestCase):
    def test_prepared_food_goes_to_prepared_partners_only(self):
        options, advice = match_orgs(load_orgs(), report(), *WESTWOOD, TUESDAY_NOON)
        self.assertTrue(options)
        self.assertEqual(advice, "")
        self.assertTrue(all("prepared" in o.accepts for o in options))
        self.assertLessEqual(len(options), 3)

    def test_unsafe_prepared_food_is_never_offered(self):
        options, advice = match_orgs(load_orgs(), report(kept_safe="no"), *WESTWOOD, TUESDAY_NOON)
        self.assertEqual(options, [])
        self.assertIn("safety", advice)

    def test_packaged_food_prefers_open_pantry_nearby(self):
        options, _ = match_orgs(load_orgs(), report(food_type="packaged", kept_safe=""), *WESTWOOD, TUESDAY_NOON)
        self.assertEqual(options[0].org_id, "ucla-cpo-food-closet")
        self.assertEqual(options[0].open_status, "Open now")

    def test_pickup_request_lists_pickup_partners(self):
        options, _ = match_orgs(load_orgs(), report(handoff="pickup"), *WESTWOOD, SUNDAY_NIGHT)
        self.assertTrue(options)
        self.assertTrue(all(o.pickup for o in options))

    def test_no_leftovers_needs_no_rescue(self):
        options, advice = match_orgs(load_orgs(), report(amount="none"), *WESTWOOD, TUESDAY_NOON)
        self.assertEqual(options, [])
        self.assertTrue(advice)

    def test_estimates(self):
        self.assertEqual(estimate("tray"), (24.0, 20))
        self.assertEqual(estimate("none"), (0.0, 0))


if __name__ == "__main__":
    unittest.main()
