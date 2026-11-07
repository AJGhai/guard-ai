"""Run: .venv\\Scripts\\python -m unittest discover -s tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rfq import (  # noqa: E402
    VENDORS,
    build_po,
    declined_changes,
    evaluate,
    po_text,
    price_range,
    reference_cost,
    scripted_exchange,
)
from tools.unit_economics import Inputs  # noqa: E402

BASE = Inputs(price=30, supplier_cost=9, shipping=1.5, duty_rate=0.12, referral_rate=0.15,
              fba_fee=5.4, ad_rate=0.10)
CHANGES = [
    {"title": "White lining", "unit_cost_delta": 1.0, "price_delta": 3.0, "supplier_ask": "Add a lining."},
    {"title": "Sizes up to 3XL", "unit_cost_delta": 0.5, "price_delta": 1.0, "supplier_ask": "Add sizes up to 3XL."},
]
BY_KEY = {v.key: v for v in VENDORS}


class Rfq(unittest.TestCase):
    def test_reference_cost(self):
        self.assertAlmostEqual(reference_cost(9, CHANGES), 10.5)

    def test_declines_what_vendor_cannot_make(self):
        self.assertEqual(declined_changes(BY_KEY["dhaka"], CHANGES), ["Sizes up to 3XL"])
        self.assertEqual(declined_changes(BY_KEY["hoabinh"], CHANGES), [])

    def test_scripted_price_never_below_floor_or_above_open(self):
        ref = reference_cost(9, CHANGES)
        for v in VENDORS:
            open_price, floor = price_range(v, ref)
            q = scripted_exchange(v, ref, CHANGES, 500, 60, "linen pants")["quote"]
            self.assertGreaterEqual(q["unit_price"], floor - 1e-9)
            self.assertLessEqual(q["unit_price"], open_price + 1e-9)

    def test_cheapest_fob_is_not_cheapest_landed_when_duty_is_high(self):
        ref = reference_cost(9, CHANGES)
        quotes = [scripted_exchange(v, ref, CHANGES, 1000, 90, "p")["quote"] for v in VENDORS]
        rows = evaluate(quotes, BASE, 4.0, 1000, 90, {})
        fob = min(rows, key=lambda r: r["unit_price"])
        self.assertEqual(fob["vendor"], "dongguan")  # cheapest FOB
        self.assertNotEqual(rows[0]["vendor"], "dongguan")  # not the best once duty is counted

    def test_flags_and_ranking(self):
        ref = reference_cost(9, CHANGES)
        quotes = [scripted_exchange(v, ref, CHANGES, 500, 40, "p")["quote"] for v in VENDORS]
        rows = {r["vendor"]: r for r in evaluate(quotes, BASE, 4.0, 500, 40, {})}
        self.assertFalse(rows["dongguan"]["flags"]["moq_ok"])   # MOQ 1000 > 500
        self.assertFalse(rows["dhaka"]["flags"]["lead_ok"])     # 60 days > 40
        self.assertFalse(rows["dhaka"]["flags"]["changes_ok"])  # cannot do 3XL
        self.assertTrue(rows["hoabinh"]["eligible"])
        first = evaluate(quotes, BASE, 4.0, 500, 40, {})[0]
        self.assertEqual(first["vendor"], "hoabinh")

    def test_po_total(self):
        ref = reference_cost(9, CHANGES)
        q = scripted_exchange(BY_KEY["hoabinh"], ref, CHANGES, 500, 60, "p")["quote"]
        row = evaluate([q], BASE, 4.0, 500, 60, {})[0]
        po = build_po(row, "linen pants", CHANGES, 500)
        self.assertAlmostEqual(po["total"], round(500 * q["unit_price"], 2))
        self.assertIn("DRAFT", po_text(po))


if __name__ == "__main__":
    unittest.main()
