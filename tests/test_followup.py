"""Unit tests for tools/food_rescue.py and the Follow-up agent's flow.  Run:  python -m unittest discover tests

The organizations below are made-up test fixtures, not real data.
"""

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from shared.models import FollowupSchedule, UserNotice, UserReply
from tools import food_rescue as fr

LA = ZoneInfo("America/Los_Angeles")
UCLA = (34.0689, -118.4452)
SAT_NOON = datetime(2026, 10, 24, 12, 0, tzinfo=LA)  # a Saturday
WEEK = {d: [["09:00", "17:00"]] for d in fr.DAYS}

ORGS = [
    {"org_id": "near-pantry", "name": "Near Pantry", "address": "1 Near St", "lat": 34.07, "lng": -118.44,
     "hours": WEEK, "accepts": ["packaged"], "email": "a@test", "pickup": True},
    {"org_id": "far-kitchen", "name": "Far Kitchen", "address": "2 Far Ave", "lat": 34.10, "lng": -118.30,
     "hours": WEEK, "accepts": ["prepared", "packaged"], "email": "b@test", "pickup": False},
    {"org_id": "mid-rescue", "name": "Mid Rescue", "address": "3 Mid Blvd", "lat": 34.06, "lng": -118.40,
     "hours": WEEK, "accepts": ["prepared"], "email": "c@test", "pickup": True},
    {"org_id": "weekday-only", "name": "Weekday Only", "address": "4 Wk Rd", "lat": 34.07, "lng": -118.44,
     "hours": {"mon": [["09:00", "17:00"]]}, "accepts": ["prepared"], "email": "d@test", "pickup": True},
]


REAL_MATCH = fr.match


class MatchTest(unittest.TestCase):
    def ids(self, *args):
        return [o["org_id"] for o in fr.match(ORGS, *args)]

    def test_hot_food_never_goes_to_a_packaged_only_pantry(self):
        self.assertEqual(self.ids("hot", "dropoff", *UCLA, SAT_NOON), ["mid-rescue", "far-kitchen"])

    def test_packaged_nearest_first(self):
        self.assertEqual(self.ids("packaged", "dropoff", *UCLA, SAT_NOON), ["near-pantry", "far-kitchen"])

    def test_pickup_requires_an_org_that_picks_up(self):
        self.assertEqual(self.ids("hot", "pickup", *UCLA, SAT_NOON), ["mid-rescue"])

    def test_closed_orgs_are_skipped(self):
        self.assertEqual(self.ids("hot", "dropoff", *UCLA, SAT_NOON.replace(hour=18)), [])
        self.assertNotIn("weekday-only", self.ids("hot", "dropoff", *UCLA, SAT_NOON))

    def test_pickup_minimum_weight(self):
        orgs = [dict(ORGS[2], pickup_min_lbs=50)]
        self.assertEqual(fr.match(orgs, "hot", "pickup", *UCLA, SAT_NOON, lbs=8), [])
        self.assertEqual(len(fr.match(orgs, "hot", "pickup", *UCLA, SAT_NOON, lbs=60)), 1)
        self.assertEqual(len(fr.match(orgs, "hot", "dropoff", *UCLA, SAT_NOON, lbs=8)), 1)

    def test_shipped_orgs_are_complete(self):
        for o in fr.load_orgs():
            self.assertTrue({"org_id", "name", "address", "lat", "lng", "hours", "accepts", "source"} <= set(o), o["org_id"])
            self.assertTrue(set(o["accepts"]) <= {"prepared", "packaged", "produce"}, o["org_id"])
            for spans in o["hours"].values():
                self.assertTrue(all(s < e for s, e in spans), o["org_id"])

    def test_unknown_food_type_matches_nothing(self):
        self.assertEqual(self.ids("mystery", "dropoff", *UCLA, SAT_NOON), [])

    def test_no_coordinates_still_matches(self):
        found = fr.match(ORGS, "hot", "dropoff", None, None, SAT_NOON)
        self.assertEqual({o["org_id"] for o in found}, {"mid-rescue", "far-kitchen"})
        self.assertTrue(all(o["distance_mi"] is None for o in found))

    def test_impact_matches_the_build_plan_example(self):
        self.assertEqual(fr.impact("trays"), (24, 20))  # "~24 lbs rescued ≈ 20 meals"
        self.assertEqual(fr.impact("none"), (0, 0))

    def test_shipped_data_file_loads(self):
        self.assertIsInstance(fr.load_orgs(), list)


class Store(dict):
    def set(self, k, v):
        self[k] = json.loads(json.dumps(v))  # like ctx.storage: values must be JSON

    def remove(self, k):
        self.pop(k, None)


class FlowTest(unittest.TestCase):
    """The agent's handlers with a fake ctx; nothing leaves the machine."""

    def setUp(self):
        import agents.followup as fu
        self.fu = fu
        self.sent, self.emails = [], []
        log = SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, exception=lambda *a: None)

        async def send(to, msg):
            self.sent.append((to, msg))

        self.ctx = SimpleNamespace(storage=Store(), send=send, logger=log)

        async def fake_email(subject, html, text, **kw):
            self.emails.append(subject)
            return "email_1"

        for target, value in (("send_email", fake_email),):
            p = mock.patch.object(fu, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(fu.food_rescue, "load_orgs", lambda: ORGS)
        p.start()
        self.addCleanup(p.stop)

    def run_(self, coro):
        return asyncio.run(coro)

    def notices(self):
        return [m for to, m in self.sent if isinstance(m, UserNotice)]

    def schedule(self, event_dt: datetime):
        m = FollowupSchedule(rfq_id="rfq_1", user_address="agent1quser", vendor_id="demo-bruin-tacos",
                             vendor_name="Bruin Tacos", event_date=event_dt.date().isoformat(),
                             event_time=event_dt.strftime("%H:%M"), location="Pauley Pavilion", lat=UCLA[0],
                             lng=UCLA[1], headcount=60)
        self.run_(self.fu.on_schedule(self.ctx, self.fu.BOOKER_ADDRESS, m))

    def reply(self, **sel):
        sel = {"rfq_id": "rfq_1", "vendor_id": "demo-bruin-tacos", **sel}
        r = UserReply(rfq_id="rfq_1", user_address="agent1quser", selection=json.dumps(sel))
        self.run_(self.fu.on_reply(self.ctx, self.fu.CONCIERGE_ADDRESS, r))

    def at(self, when: datetime):
        with mock.patch.object(self.fu, "datetime", wraps=datetime) as dt:
            dt.now = lambda tz=None: when.astimezone(tz) if tz else when
            dt.fromisoformat = datetime.fromisoformat
            self.run_(self.fu.run_due(self.ctx))

    def ev(self):
        return self.ctx.storage["event:rfq_1"]

    def test_only_the_booker_can_schedule(self):
        m = FollowupSchedule(rfq_id="rfq_x", user_address="u", vendor_id="v", vendor_name="V", event_date="2026-12-01")
        self.run_(self.fu.on_schedule(self.ctx, "agent1qstranger", m))
        self.assertNotIn("event:rfq_x", self.ctx.storage)

    def test_reminder_then_checkin_then_rescue(self):
        start = (datetime.now(LA) + timedelta(days=3)).replace(hour=12, minute=0, second=0, microsecond=0)
        self.schedule(start)
        self.schedule(start)  # replay: still one event
        self.assertEqual(self.ctx.storage["events"], ["rfq_1"])

        self.at(start - timedelta(hours=23))
        self.at(start - timedelta(hours=22))  # second tick: no second reminder
        reminders = [n for n in self.notices() if n.text.startswith("Reminder")]
        self.assertEqual(len(reminders), 1)
        self.assertIn("tomorrow", reminders[0].text)  # sent at 1 PM the day before a noon event

        self.at(start + timedelta(hours=3, minutes=16))
        form = self.notices()[-1]
        self.assertEqual(form.card_kind, "form")
        self.assertEqual(json.loads(form.card_payload)["submit_cta"]["selection"]["action"], "submit_leftovers")

        at_noon = lambda orgs, food, handoff, lat, lng, now, lbs=0: REAL_MATCH(orgs, food, handoff, lat, lng, SAT_NOON, lbs)
        with mock.patch.object(self.fu.food_rescue, "match", at_noon):  # orgs open at the test's fixed time
            self.reply(action="submit_leftovers", rating="5", amount="trays", food_type="hot", kept_safe="yes",
                       handoff="dropoff", contact="team@test")
        cards = self.notices()[-1]
        self.assertEqual(cards.card_kind, "carousel")
        items = json.loads(cards.card_payload)["items"]
        self.assertEqual([i["id"] for i in items], ["mid-rescue", "far-kitchen"])
        self.assertTrue(all({"rfq_id", "vendor_id", "org_id"} <= set(i["primary_cta"]["selection"]) for i in items))

        self.reply(action="pick_rescue", org_id="mid-rescue")
        self.reply(action="pick_rescue", org_id="mid-rescue")  # double tap: one email
        self.assertEqual(len(self.emails), 1)
        self.assertEqual(self.ev()["stage"], "rescued")
        self.assertEqual(self.ev()["impact"]["meals_est"], 20)
        self.assertEqual(self.ev()["rating"], 5)
        self.assertIn("≈ 20 meals", self.notices()[-1].text)
        self.assertIn("google.com/maps/dir", self.notices()[-1].text)

    def test_no_reminder_when_booked_inside_the_window(self):
        self.schedule(datetime.now(LA) + timedelta(hours=5))
        self.assertEqual(self.ev()["remind_at"], "")

    def checked_in(self):
        self.schedule(datetime.now(LA) - timedelta(hours=4))  # already over: check-in is due
        self.at(datetime.now(timezone.utc))
        self.assertEqual(self.ev()["stage"], "checkin_sent")

    def test_unsafe_food_is_never_routed(self):
        self.checked_in()
        self.reply(action="submit_leftovers", amount="tray", food_type="hot", kept_safe="unsure", handoff="pickup")
        self.assertEqual(self.ev()["stage"], "closed")
        self.assertEqual(self.emails, [])
        self.assertIn("can't send food", self.notices()[-1].text)

    def test_no_leftovers_closes(self):
        self.checked_in()
        self.reply(action="submit_leftovers", amount="none", rating="4")
        self.assertEqual(self.ev()["stage"], "closed")
        self.assertEqual(self.ev()["rating"], 4)

    def test_missing_answers_resend_the_form(self):
        self.checked_in()
        self.reply(action="submit_leftovers", amount="tray")
        self.assertEqual(self.notices()[-1].card_kind, "form")
        self.assertEqual(self.ev()["stage"], "checkin_sent")

    def test_nothing_open_says_so_plainly(self):
        self.checked_in()
        with mock.patch.object(self.fu.food_rescue, "load_orgs", lambda: []):
            self.reply(action="submit_leftovers", amount="tray", food_type="packaged", kept_safe="yes",
                       handoff="dropoff")
        self.assertEqual(self.ev()["stage"], "closed")
        self.assertIn("couldn't find", self.notices()[-1].text)

    def test_replies_from_another_user_are_ignored(self):
        self.checked_in()
        r = UserReply(rfq_id="rfq_1", user_address="agent1qsomeoneelse",
                      selection=json.dumps({"action": "submit_leftovers", "amount": "none"}))
        self.run_(self.fu.on_reply(self.ctx, self.fu.CONCIERGE_ADDRESS, r))
        self.assertEqual(self.ev()["stage"], "checkin_sent")


if __name__ == "__main__":
    unittest.main()
