"""Offline tests for the listing/review lookup and the improvement pipeline (fake ZooData, no network).

Run: .venv/bin/python tests/test_improve.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.photo_spec as ps  # noqa: E402
import tools.improve as imp  # noqa: E402
import tools.listing as lst  # noqa: E402

PRODUCT = {
    "asin": "B0ABCDEF12", "title": "Linen Wide Leg Pants for Women", "brand": "Acme", "rating": 4.1,
    "ratingCount": 812, "features": ["Linen blend", "Elastic waist"],
    "images": ["https://img.example/main.jpg", "https://img.example/2.jpg"],
    "buyboxWinner": {"price": 27.99}, "bestsellersRank": [{"rank": 1200}, {"rank": 15}],
}
ANALYSIS = {
    "reviewCount": 812, "avgRating": 4.1,
    "consumerInsights": [
        {"element": "runs small", "labelType": "issues", "reviewRate": 0.18},
        {"element": "Runs small", "labelType": "painPoints", "reviewRate": 0.10},
        {"element": "see-through", "labelType": "painPoints", "reviewRate": 0.12},
        {"element": "soft fabric", "labelType": "positives", "reviewRate": 0.30},
        {"element": "add lining", "labelType": "improvements", "reviewRate": 0.05},
    ],
}


class FakeZooData:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, endpoint, body):
        self.calls.append((endpoint, body))
        r = self.responses[endpoint]
        if isinstance(r, Exception):
            raise r
        return r(body) if callable(r) else r


class AsinTests(unittest.TestCase):
    def test_links_and_bare_asins(self):
        cases = {
            "https://www.amazon.com/Linen-Pants/dp/B0ABCDEF12/ref=sr_1_1?th=1": "B0ABCDEF12",
            "https://amazon.com/gp/product/b0abcdef12": "B0ABCDEF12",
            "https://www.amazon.com/product-reviews/B0ABCDEF12/": "B0ABCDEF12",
            "B0ABCDEF12": "B0ABCDEF12",
            "https://www.amazon.com/s?k=linen+pants": None,
            "": None,
        }
        for text, want in cases.items():
            self.assertEqual(lst.asin_from_text(text), want, text)


class ListingTests(unittest.TestCase):
    def setUp(self):
        self._post = lst._post

    def tearDown(self):
        lst._post = self._post

    def test_listing_fields_from_realtime_product(self):
        lst._post = FakeZooData({"realtime/product": PRODUCT})
        out = lst.get_listing("B0ABCDEF12")
        self.assertEqual(out["source"], "live_zoodata")
        self.assertEqual((out["price"], out["bsr"], out["image"]), (27.99, 15, "https://img.example/main.jpg"))

    def test_listing_falls_back_to_labelled_sample(self):
        lst._post = FakeZooData({"realtime/product": lst.ZooDataError("ZooData HTTP 401")})
        out = lst.get_listing("B0ABCDEF12")
        self.assertEqual((out["source"], out["asin"]), ("sample", "B0ABCDEF12"))
        self.assertIn("401", out["note"])

    def test_review_themes_grouped_and_deduped(self):
        fake = FakeZooData({"reviews/analysis": ANALYSIS})
        lst._post = fake
        out = lst.review_themes(["B0ABCDEF12"])
        self.assertEqual([t["element"] for t in out["issues"]], ["runs small", "see-through"])
        self.assertEqual(out["positives"][0], {"element": "soft fabric", "rate": 0.3})
        self.assertEqual(fake.calls[0][1], {"mode": "asin", "asins": ["B0ABCDEF12"], "period": "6m"})

    def test_few_reviews_reads_raw_low_star(self):
        pages = {"n": 0}

        def reviews(body):
            pages["n"] += 1
            return {"reviews": [{"rating": 2, "title": "Too small", "body": "Had to size up"},
                                {"rating": 5, "title": "Love", "body": "Great"}],
                    "nextCursor": "c2" if pages["n"] == 1 else None}
        lst._post = FakeZooData({"reviews/analysis": {"consumerInsights": []}, "realtime/reviews": reviews})
        out = lst.own_reviews("B0ABCDEF12")
        self.assertEqual(out["review_count"], 4)
        self.assertEqual(len(out["raw_low_star"]), 2)
        self.assertEqual(out["avg_rating"], 3.5)

    def test_competitor_asins_skip_own_and_thin(self):
        rows = [{"asin": "A1", "rating_count": 900, "monthly_sales_floor": 100},
                {"asin": "OWN", "rating_count": 900, "monthly_sales_floor": 999},
                {"asin": "A2", "rating_count": 10, "monthly_sales_floor": 500},
                {"asin": "A3", "rating_count": 60, "monthly_sales_floor": 300}]
        self.assertEqual(lst.competitor_asins(rows, "OWN"), ["A3", "A1"])


class ComparisonTests(unittest.TestCase):
    def test_parse_comparison(self):
        out = ps.parse_comparison(json.dumps({
            "doing_well": [{"point": "Cheaper", "evidence": "$27.99 vs $29.99"}, {"point": ""}],
            "room_to_improve": [{"point": "Sizing", "evidence": "18% say runs small", "action": "Grade up"}],
        }))
        self.assertEqual(len(out["doing_well"]), 1)
        self.assertEqual(out["room_to_improve"][0]["action"], "Grade up")

    def test_rule_based_comparison_uses_numbers(self):
        you = {"price": 27.99, "rating": 3.9, "own_issues": [{"element": "runs small", "rate": 0.18}],
               "own_positives": [{"element": "soft", "rate": 0.3}]}
        market = {"price": {"median": 29.99}, "avg_rating": 4.3}
        out = ps.rule_based_comparison(you, market)
        self.assertIn("Priced competitively", [g["point"] for g in out["doing_well"]])
        self.assertIn("Rated below competitors", [g["point"] for g in out["room_to_improve"]])
        self.assertTrue(any("18%" in g["evidence"] for g in out["room_to_improve"]))


class AnnotateTests(unittest.TestCase):
    def test_annotated_image_is_wider_jpeg(self):
        import io
        from PIL import Image
        from tools.annotate import annotate
        from tools.spec_sheet import sample_photo
        items = [{"title": "Add lining", "region": "lower_body", "fixes": "sheer"},
                 {"title": "Sizes to 3XL", "region": "whole", "fixes": "runs small"},
                 {"title": "Odd", "region": "nowhere"}]
        out = annotate(sample_photo(), items, "#00a374", title="After")
        self.assertTrue(out.startswith(b"\xff\xd8"))
        w, h = Image.open(io.BytesIO(out)).size
        self.assertEqual(h, 900)
        self.assertGreater(w, 900 * 600 / 800)           # photo plus the callout panel
        self.assertTrue(annotate(sample_photo(), [], "#00a374").startswith(b"\xff\xd8"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(imp, k) for k in
                      ("get_listing", "own_reviews", "analyze_photo", "market_picture", "review_themes",
                       "compare_with_competitors", "fetch_image", "gather_reviews", "advise_evidence")}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(imp, k, v)

    def test_link_mode_runs_every_step_in_order(self):
        seen = {}
        imp.get_listing = lambda asin: {"source": "live_zoodata", "asin": asin, "title": "Linen pants",
                                        "price": 27.99, "rating": 4.1, "rating_count": 812, "image": "u", "features": []}
        imp.own_reviews = lambda asin: {"issues": [{"element": "runs small", "rate": 0.18}], "positives": []}
        imp.fetch_image = lambda url: b"jpeg"

        def analyze(jpeg, complaints, description, context):
            seen["context"], seen["jpeg"] = context, jpeg
            return {**ps.SAMPLE_SPEC, "search_terms": "linen pants"}
        imp.analyze_photo = analyze
        imp.market_picture = lambda terms: {"source": "live_zoodata", "n": 2, "price": {"median": 29.99},
                                            "avg_rating": 4.3, "listings": [
                                                {"asin": "B0ABCDEF12", "rating_count": 812, "monthly_sales_floor": 9},
                                                {"asin": "C1", "rating_count": 500, "monthly_sales_floor": 5}]}
        imp.review_themes = lambda asins, sample=None: seen.setdefault("comp_asins", asins) and {"positives": [], "issues": []}

        def compare(you, comps, market):
            seen["you"] = you
            return {"doing_well": [], "room_to_improve": [], "source": "zoowork_agent"}
        imp.compare_with_competitors = compare
        imp.gather_reviews = lambda asins: {}                           # no network; too little evidence
        steps = []
        out = imp.run_pipeline("link", link="https://www.amazon.com/dp/B0ABCDEF12", on_step=steps.append)
        self.assertEqual(seen["jpeg"], b"jpeg")
        self.assertEqual(seen["context"]["own_review_themes"]["issues"][0]["element"], "runs small")
        self.assertEqual(seen["comp_asins"], ["C1"])                    # own ASIN excluded
        self.assertEqual(seen["you"]["price"], 27.99)                   # listing price wins
        self.assertEqual(len(steps), 8)                                 # includes the review-evidence step
        self.assertIsNone(out["grounded"])                              # too few reviews: keeps the photo agent's changes
        self.assertEqual(out["terms"], "linen pants")

    def test_evidence_step_replaces_improvements_with_cited_ones(self):
        imp.get_listing = lambda asin: {"source": "live_zoodata", "asin": asin, "title": "Linen pants", "price": 27.99,
                                        "rating": 4.1, "rating_count": 812, "image": "u", "features": []}
        imp.own_reviews = lambda asin: {"issues": [], "positives": []}
        imp.fetch_image = lambda url: b"jpeg"
        imp.analyze_photo = lambda *a: {**ps.SAMPLE_SPEC, "search_terms": "linen pants"}
        imp.market_picture = lambda terms: {"source": "live_zoodata", "n": 1, "price": {"median": 29.99},
                                            "avg_rating": 4.3, "listings": [{"asin": "C1", "monthly_sales_floor": 5}]}
        imp.review_themes = lambda asins, sample=None: {"positives": [], "issues": []}
        imp.compare_with_competitors = lambda you, comps, market: {"doing_well": [], "room_to_improve": [], "source": "zoowork_agent"}

        def low(n):
            return {"title": "t", "body": f"too thin {n}", "rating": 2, "helpful": n, "verified": True, "date": None,
                    "summary": None, "tags": [["mentioned_issues", "Thin Material"]]}
        imp.gather_reviews = lambda asins: {a: {"low": [low(i) for i in range(6)], "high": []} for a in asins}
        imp.advise_evidence = lambda product, evidence, econ, market: {
            "source": "zoowork_agent", "summary": "s",
            "improvements": [{"title": "Add lining", "observation": "Fabric is too thin", "region": "lower_body",
                              "unit_cost_delta": 0.8, "price_delta": 2.0, "confidence": "high", "risk": "r",
                              "supplier_ask": "a", "themes": ["Thin Material"], "review_ids": ["R1"],
                              "support": {"count": 6, "of": 12, "share": 0.5, "avg_rating": 2.0, "review_ids": ["R1"]}}]}
        out = imp.run_pipeline("link", link="https://www.amazon.com/dp/B0ABCDEF12")
        self.assertEqual([i["title"] for i in out["spec"]["improvements"]], ["Add lining"])
        self.assertEqual(out["spec"]["improvements"][0]["fixes"], "Fabric is too thin")
        self.assertEqual(out["evidence"]["n_low"], 12)                  # own listing + one competitor, 6 each

    def test_link_without_asin_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            imp.run_pipeline("link", link="https://www.amazon.com/s?k=pants")

    def test_auto_select(self):
        imps = [{"confidence": "low"}, {"confidence": "medium"}, {"confidence": "high"}]
        self.assertEqual(imp.auto_select(imps), [2, 3])
        self.assertEqual(imp.auto_select([{"confidence": "low"}]), [1])


if __name__ == "__main__":
    os.environ.pop("ZOODATA_API_KEY", None)
    unittest.main()
