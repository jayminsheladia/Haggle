"""Unit tests for routing owner texts in vendors/owner_inbox.py.  Run:  python -m unittest discover tests"""

import unittest

from vendors.owner_inbox import route
from vendors.template import parse_owner_decision

A1 = ("demo-bruin-tacos", {"approval_id": "A1234", "rfq_id": "rfq_1"})
A2 = ("demo-green-fork", {"approval_id": "A5678", "rfq_id": "rfq_2"})


class RouteTests(unittest.TestCase):
    def test_bare_reply_answers_the_only_pending_request(self):
        self.assertEqual(route("YES", [A1]), (A1, ""))

    def test_id_picks_the_request(self):
        self.assertEqual(route("counter a5678 750", [A1, A2]), (A2, ""))

    def test_bare_reply_with_several_pending_asks_for_the_id(self):
        target, why = route("no", [A1, A2])
        self.assertIsNone(target)
        self.assertIn("A1234", why)

    def test_unknown_id(self):
        target, why = route("YES A9999", [A1])
        self.assertIsNone(target)
        self.assertIn("A9999", why)

    def test_nothing_pending_is_ignored_quietly(self):
        self.assertEqual(route("YES", []), (None, ""))

    def test_text_without_the_id_parses_as_a_decision(self):
        self.assertEqual(parse_owner_decision("counter  750"), ("counter", 750.0))
        self.assertEqual(parse_owner_decision("yes"), ("approve", None))
        self.assertEqual(parse_owner_decision("NO"), ("decline", None))


if __name__ == "__main__":
    unittest.main()
