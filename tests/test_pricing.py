"""Unit tests for vendors/pricing.py.  Run:  python -m unittest discover tests"""

import unittest
from datetime import datetime

from shared.menu_schema import MenuItem, OwnerRules, VendorMenu
from shared.models import RFQ
from vendors.pricing import check_rules, counter_response, floor_price, price_for

NOW = datetime(2026, 10, 17, 12, 0)


def menu(**kw) -> VendorMenu:
    items = [
        MenuItem(name="Chicken plate", price_per_person=16, dietary_tags=["halal"]),
        MenuItem(name="Beef plate", price_per_person=18),
        MenuItem(name="Falafel plate", price_per_person=12, dietary_tags=["vegetarian", "vegan"]),
        MenuItem(name="Pesto pasta", price_per_person=14, dietary_tags=["vegetarian", "contains-nuts"]),
        MenuItem(name="Lemonade", price_per_person=3),
    ]
    return VendorMenu(name="Test", slug="test", items=kw.pop("items", items), **kw)


def rfq(**kw) -> RFQ:
    base = dict(rfq_id="r1", event_date="2026-10-23", event_time="18:00", headcount=60, budget_usd=900)
    return RFQ(**{**base, **kw})


class PriceFor(unittest.TestCase):
    def test_groups_priced_at_average_of_fitting_items(self):
        total, lines, ok, conflicts = price_for(menu(), rfq(dietary={"vegetarian": 10, "halal": 3}))
        self.assertTrue(ok, conflicts)
        by = {li.group: li for li in lines}
        self.assertEqual(by["standard"].headcount, 47)
        self.assertEqual(by["standard"].per_head_usd, 15.0)  # mean of 16, 18, 12, 14 (lemonade is an extra)
        self.assertEqual(by["vegetarian"].per_head_usd, 13.0)
        self.assertEqual(by["halal"].per_head_usd, 16.0)
        self.assertEqual(total, 47 * 15 + 10 * 13 + 3 * 16)

    def test_cheap_add_ons_dont_price_the_meal(self):
        m = menu(items=[MenuItem(name="Tri-Tip Dinner", price_per_person=28), MenuItem(name="Chicken Dinner", price_per_person=25),
                        MenuItem(name="Coleslaw", price_per_person=2.75), MenuItem(name="Garden Salad", price_per_person=2.5)])
        _, lines, _, _ = price_for(m, rfq(headcount=10))
        self.assertEqual(lines[0].per_head_usd, 26.5)

    def test_dietary_conflict(self):
        _, _, ok, conflicts = price_for(menu(), rfq(dietary={"kosher": 5}))
        self.assertFalse(ok)
        self.assertIn("no kosher items", conflicts)

    def test_unlabeled_public_menu_is_priced_and_flagged(self):
        total, lines, ok, conflicts = price_for(menu(), rfq(dietary={"kosher": 5}), unlabeled_ok=True)
        self.assertFalse(ok)
        self.assertIn("kosher options not labeled on the menu", conflicts)
        self.assertEqual(next(li for li in lines if li.group == "kosher").per_head_usd, 15.0)  # whole-menu average

    def test_allergy_removes_items_and_unknown_allergen_is_a_conflict(self):
        _, lines, ok, _ = price_for(menu(), rfq(dietary={"vegetarian": 10}, allergies=["nuts"]))
        veg = next(li for li in lines if li.group == "vegetarian")
        self.assertEqual(veg.per_head_usd, 12.0)  # pesto pasta dropped
        self.assertTrue(ok)
        _, _, ok, conflicts = price_for(menu(), rfq(allergies=["shellfish"]))
        self.assertFalse(ok)
        self.assertIn("can't confirm shellfish-free options", conflicts)

    def test_extras_priced_only_if_listed(self):
        _, lines, _, _ = price_for(menu(), rfq(extras=["drinks", "dessert"]))
        by = {li.group: li for li in lines}
        self.assertEqual(by["drinks"].per_head_usd, 3.0)
        self.assertEqual(by["dessert"].package, "not offered")
        self.assertEqual(by["dessert"].subtotal_usd, 0)

    def test_delivery_fee_added(self):
        total, _, _, _ = price_for(menu(rules=OwnerRules(delivery_fee_usd=40)), rfq(headcount=10))
        self.assertEqual(total, 10 * 15 + 40)

    def test_menu_without_prices_is_never_priced(self):
        total, lines, ok, conflicts = price_for(menu(items=[MenuItem(name="Tacos")]), rfq(headcount=20))
        self.assertEqual((total, lines, ok), (0.0, [], False))
        self.assertIn("no public menu prices", conflicts)


class Rules(unittest.TestCase):
    def test_all_rules(self):
        rules = OwnerRules(min_headcount=20, max_headcount=50, lead_time_hours=48, delivery_radius_mi=5,
                           blackout_dates=["2026-10-18"])
        self.assertEqual(check_rules(rules, rfq(headcount=30, distance_mi=2), NOW), [])
        self.assertIn("maximum headcount is 50", check_rules(rules, rfq(headcount=60), NOW))
        self.assertIn("minimum headcount is 20", check_rules(rules, rfq(headcount=5), NOW))
        self.assertIn("needs 48h notice", check_rules(rules, rfq(headcount=30, event_date="2026-10-18"), NOW))
        self.assertIn("unavailable on 2026-10-18", check_rules(rules, rfq(headcount=30, event_date="2026-10-18"), NOW))
        self.assertIn("outside delivery radius (5 mi)", check_rules(rules, rfq(headcount=30, distance_mi=8), NOW))

    def test_radius_from_coordinates(self):
        rules = OwnerRules(delivery_radius_mi=5, lead_time_hours=0)
        far = check_rules(rules, rfq(lat=34.07, lng=-118.44), NOW, vendor_lat=33.94, vendor_lng=-118.40)
        self.assertTrue(any("radius" in v for v in far))


class Counter(unittest.TestCase):
    def test_floor_depends_on_order_size(self):
        rules = OwnerRules(floor_discount_pct=12)
        self.assertEqual(floor_price(300, rules), 300.0)  # small order: no discount
        self.assertEqual(floor_price(600, rules), 564.0)  # mid: half of 12% = 6%
        self.assertEqual(floor_price(1000, rules), 904.0)  # 80% of 12% = 9.6%
        self.assertEqual(floor_price(2000, rules), 1760.0)  # big: the full 12%

    def test_accept_at_or_above_floor(self):
        self.assertEqual(counter_response(1000, 950, 904, 1), ("accept", 950))
        self.assertEqual(counter_response(1000, 904, 904, 1), ("accept", 904))

    def test_meet_halfway_then_best_then_decline(self):
        self.assertEqual(counter_response(1000, 800, 904, 1), ("counter", 904))  # halfway 900 is below the floor
        self.assertEqual(counter_response(1000, 850, 880, 1), ("counter", 925.0))
        self.assertEqual(counter_response(925, 850, 880, 2), ("best", 880))
        self.assertEqual(counter_response(880, 850, 870, 3), ("decline", "floor reached"))

    def test_no_room_keeps_the_price_then_owner(self):
        self.assertEqual(counter_response(300, 250, 300, 1), ("no_room", 300))
        self.assertEqual(counter_response(300, 250, 300, 2), ("no_room", 300))
        self.assertEqual(counter_response(773.14, 749.95, 773.14, 3), ("decline", "floor reached"))


if __name__ == "__main__":
    unittest.main()
