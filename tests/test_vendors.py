"""Run: .venv\Scripts\python -m unittest discover -s tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.sourcing import run_sourcing  # noqa: E402
from tools.rfq import VENDORS, build_po, evaluate, vendor_message  # noqa: E402
from tools.unit_economics import Inputs  # noqa: E402
from tools.vendors import SAMPLE_SETS, TEMPLATE_CSV, load_sample_set, parse_vendor_sheet  # noqa: E402

BASE = Inputs(price=24.99, supplier_cost=9.0, shipping=1.5, duty_rate=0.12, referral_rate=0.15, fba_fee=5.4, ad_rate=0.10)
CHANGES = [{"title": "Pre-shrunk fabric", "unit_cost_delta": 0.8, "price_delta": 2.0, "supplier_ask": "Pre-washed fabric?"}]


class VendorSheet(unittest.TestCase):
    def test_template_parses(self):
        vendors, warnings = parse_vendor_sheet(TEMPLATE_CSV.encode(), "t.csv", 9.0)
        self.assertEqual([v.name for v in vendors], ["Sunrise Apparel Co", "Lotus Textile Works"])
        self.assertEqual(warnings, [])
        self.assertTrue(all(v.source == "yours" for v in vendors))
        self.assertAlmostEqual(vendors[0].open_mult * 9.0, 9.40)

    def test_flexible_headers_and_defaults(self):
        csv = b"Supplier Name,Price per unit,Min Order\nAcme,8.5,300\nBeta Co,$9.25,\n"
        vendors, warnings = parse_vendor_sheet(csv, "x.csv", 9.0)
        self.assertEqual([v.name for v in vendors], ["Acme", "Beta Co"])
        self.assertEqual(vendors[1].moq, 500)            # missing minimum order defaults
        self.assertTrue(any("Beta Co" in w and "minimum" in w for w in warnings))

    def test_missing_required_columns_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            parse_vendor_sheet(b"name,country\nAcme,Vietnam\n", "x.csv", 9.0)

    def test_rows_without_price_are_skipped_with_a_warning(self):
        vendors, warnings = parse_vendor_sheet(b"name,unit_price\nAcme,8.5\nBad,\n", "x.csv", 9.0)
        self.assertEqual([v.name for v in vendors], ["Acme"])
        self.assertTrue(any("Bad" in w for w in warnings))

    def test_at_most_five_vendors(self):
        rows = "\n".join(f"V{i},{8 + i * 0.1}" for i in range(8))
        vendors, warnings = parse_vendor_sheet(("name,unit_price\n" + rows).encode(), "x.csv", 9.0)
        self.assertEqual(len(vendors), 5)
        self.assertTrue(any("first 5" in w for w in warnings))

    def test_every_sample_set_loads_and_is_fictional_and_distinct_from_builtins(self):
        builtin = {v.name.lower() for v in VENDORS}
        for label in SAMPLE_SETS:
            vendors, warnings = load_sample_set(label, 9.0)
            self.assertTrue(3 <= len(vendors) <= 5, label)
            self.assertEqual(warnings, [], label)
            self.assertFalse({v.name.lower() for v in vendors} & builtin, label)


class WithCustomVendors(unittest.TestCase):
    def setUp(self):
        self.vendors, _ = load_sample_set("Vietnam and India apparel vendors", 9.0)

    def test_scripted_quotes_are_for_exactly_these_vendors(self):
        r = run_sourcing("pants", CHANGES, 9.8, 500, 60, use_agents=False, vendors=self.vendors)
        self.assertEqual({q["vendor"] for q in r["quotes"]}, {v.key for v in self.vendors})
        self.assertEqual(set(r["chats"]), {v.key for v in self.vendors})

    def test_evaluate_ranks_uploaded_vendors_and_flags_moq(self):
        r = run_sourcing("pants", CHANGES, 9.8, 500, 60, use_agents=False, vendors=self.vendors)
        rows = {x["vendor"]: x for x in evaluate(r["quotes"], BASE, 2.0, 500, 60, {}, vendors=self.vendors)}
        lotus = next(v for v in self.vendors if "lotus" in v.key)
        self.assertFalse(rows[lotus.key]["flags"]["moq_ok"])       # their minimum is 800
        self.assertTrue(all(rows[k]["duty_rate"] == 0.12 for k in rows))

    def test_default_vendors_still_work(self):
        r = run_sourcing("pants", CHANGES, 9.8, 500, 60, use_agents=False)
        self.assertEqual({q["vendor"] for q in r["quotes"]}, {v.key for v in VENDORS})


class SupplierMessage(unittest.TestCase):
    def test_message_has_the_order_and_the_contact(self):
        vendors, _ = load_sample_set("Vietnam and India apparel vendors", 9.0)
        r = run_sourcing("pants", CHANGES, 9.8, 500, 60, use_agents=False, vendors=vendors)
        row = evaluate(r["quotes"], BASE, 2.0, 500, 60, {}, vendors=vendors)[0]
        po = build_po(row, "Women's pants", CHANGES, 500)
        text = vendor_message(po, "sales@x.example.com")
        for part in (po["po_number"], "sales@x.example.com", "500 units", f"${po['total']:,.2f}", "Pre-shrunk fabric",
                     "pro forma invoice", row["name"]):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
