"""Run: .venv\\Scripts\\python -m unittest discover -s tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.unit_economics import (  # noqa: E402
    Inputs,
    apply_changes,
    apply_event,
    compute,
    price_to_hold_margin,
    supplier_cost_to_hold_margin,
)

BASE = Inputs(price=40, supplier_cost=10, shipping=2, duty_rate=0.10, referral_rate=0.15,
              fba_fee=5, ad_rate=0.10)


class UnitEconomics(unittest.TestCase):
    def test_compute(self):
        r = compute(BASE)
        # landed 10 + 2 + 1 = 13; referral 6; fba 5; ads 4 -> profit 40-13-6-5-4 = 12
        self.assertAlmostEqual(r["landed"], 13)
        self.assertAlmostEqual(r["profit"], 12)
        self.assertAlmostEqual(r["margin"], 0.30)
        self.assertAlmostEqual(sum(r["parts"].values()) + r["profit"], 40)

    def test_duty_event_lowers_margin(self):
        hit = compute(apply_event(BASE, "duty_up"))
        self.assertAlmostEqual(hit["profit"], 12 - 2.5)  # +25 pts on a $10 cost

    def test_price_to_hold_margin_restores_target(self):
        hit = apply_event(BASE, "duty_up")
        p = price_to_hold_margin(hit, 0.30)
        restored = compute(Inputs(**{**hit.__dict__, "price": p}))
        self.assertAlmostEqual(restored["margin"], 0.30)

    def test_supplier_cost_to_hold_margin_restores_target(self):
        hit = apply_event(BASE, "freight_up")
        c = supplier_cost_to_hold_margin(hit, 0.30)
        restored = compute(Inputs(**{**hit.__dict__, "supplier_cost": c}))
        self.assertAlmostEqual(restored["margin"], 0.30)

    def test_impossible_target_returns_none(self):
        self.assertIsNone(price_to_hold_margin(BASE, 0.80))

    def test_apply_changes(self):
        r = compute(apply_changes(BASE, 1.0, 3.0))
        self.assertAlmostEqual(r["price"], 43)


if __name__ == "__main__":
    unittest.main()
