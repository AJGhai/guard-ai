"""Run: .venv\\Scripts\\python -m unittest discover -s tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.reviews import _price_value, build_evidence, parse_asin, prompt_pack, rule_based, support  # noqa: E402


def review(title, body, rating, helpful, tags):
    return {"title": title, "body": body, "rating": rating, "helpful": helpful, "verified": True,
            "date": None, "summary": None, "tags": [list(t) for t in tags]}


LOW = [
    review("Shrank", "Great until I washed them and they shrank a lot.", 2, 50,
           [("mentioned_issues", "Significant Shrinkage, Short Length"), ("mentioned_improvements", "Improve Shrinkage Resistance")]),
    review("See through", "White pair is see-through.", 1, 30, [("mentioned_issues", "See-Through Fabric")]),
    review("Too small", "Had to order two sizes up.", 3, 10, [("mentioned_improvements", "Improve Fit"), ("mentioned_pain_points", "Return Hassle")]),
    review("Shrunk", "Shrunk after one wash.", 2, 5, [("mentioned_issues", "Significant Shrinkage")]),
]
HIGH = [review("Love", "Lovely drape.", 5, 9, [("mentioned_positives", "Good Drape, Lightweight Fabric")])]
COMPS = [{"asin": "B000000001", "name": "Brand A pants", "role": "competitor"}]
DATA = {"B000000001": {"low": LOW, "high": HIGH}}


class Evidence(unittest.TestCase):
    def test_parse_asin(self):
        self.assertEqual(parse_asin("https://www.amazon.com/Some-Name/dp/B0F9KYFRB6/ref=sr_1_1?x=1"), "B0F9KYFRB6")
        self.assertEqual(parse_asin("b0f9kyfrb6"), "B0F9KYFRB6")
        self.assertIsNone(parse_asin("linen pants"))
        self.assertIsNone(parse_asin("abcdefghij"))  # ten letters but no digit: not an ASIN

    def test_price_value_accepts_zoodata_object_and_numbers(self):
        self.assertEqual(_price_value({"symbol": "$", "currency": "USD", "value": 24.99, "raw": "$24.99"}), 24.99)
        self.assertEqual(_price_value(19), 19.0)
        self.assertIsNone(_price_value(None))
        self.assertIsNone(_price_value({"raw": "n/a"}))

    def test_themes_are_counted_per_review_and_split_on_commas(self):
        ev = build_evidence(COMPS, DATA)
        self.assertEqual(ev["n_low"], 4)
        by = {t["name"]: t for t in ev["themes"]}
        self.assertEqual(by["Significant Shrinkage"]["count"], 2)  # two reviews, not three tags
        self.assertIn("Short Length", by)  # split from "Significant Shrinkage, Short Length"
        self.assertAlmostEqual(by["Significant Shrinkage"]["share"], 0.5)
        self.assertAlmostEqual(by["Significant Shrinkage"]["avg_rating"], 2.0)

    def test_reviews_get_ids_by_helpfulness(self):
        ev = build_evidence(COMPS, DATA)
        self.assertEqual(ev["reviews"]["R1"]["helpful"], 50)
        self.assertEqual(ev["reviews"]["R2"]["helpful"], 30)

    def test_support_counts_union_of_themes_and_ids(self):
        ev = build_evidence(COMPS, DATA)
        s = support(ev, ["Significant Shrinkage", "Improve Shrinkage Resistance"], [])
        self.assertEqual(s["count"], 2)  # R1 has both tags, R4 has one: union is 2 reviews
        s2 = support(ev, ["Improve Fit"], ["R2", "R999"])  # unknown ids are ignored
        self.assertEqual(s2["count"], 2)
        self.assertEqual(s2["of"], 4)

    def test_positives_come_from_high_star_reviews(self):
        ev = build_evidence(COMPS, DATA)
        self.assertEqual({p["name"] for p in ev["positives"]}, {"Good Drape", "Lightweight Fabric"})

    def test_prompt_pack_only_has_known_ids(self):
        ev = build_evidence(COMPS, DATA)
        pack = prompt_pack(ev)
        self.assertTrue(all(r["id"] in ev["reviews"] for r in pack["reviews"]))

    def test_rule_based_is_grounded_in_themes(self):
        ev = build_evidence(COMPS, DATA)
        titles = [i["title"] for i in rule_based(ev)]
        self.assertTrue(any("shrink" in t.lower() for t in titles))
        for i in rule_based(ev):
            self.assertTrue(support(ev, i["themes"], [])["count"] > 0)


if __name__ == "__main__":
    unittest.main()
