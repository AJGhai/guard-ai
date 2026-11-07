"""Request-for-quote core for Product-to-PO Step 2: vendors, landed cost, ranking, purchase order.

Everything a person relies on is computed here, deterministically:
  - what each vendor would cost landed (FOB quote + shipping + duty) and earn per unit,
  - which vendors can actually do the job (MOQ, lead time, every requested change),
  - the ranking, and the purchase-order draft.

The three vendors are SIMULATED. Their opening prices, floors, minimum orders and so on are
demo assumptions, and so are the duty rates (editable in the UI). Nothing is ever sent: an
approved purchase order is written to data/outbox/po/ and goes nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from tools.supplier import OUTBOX
from tools.unit_economics import Inputs, compute

PO_DIR = OUTBOX / "po"


@dataclass(frozen=True)
class Vendor:
    key: str
    name: str
    country: str
    open_mult: float  # opening price as a multiple of the reference cost
    floor_mult: float  # lowest price they would ever accept, same scale
    moq: int
    lead_days: int
    certs: tuple[str, ...]
    payment: str
    duty_rate: float  # ASSUMED for the demo, editable in the UI
    personality: str
    cannot: tuple[str, ...] = ()  # keywords of changes this vendor cannot make
    cannot_text: str = ""  # the same restriction in plain English, for the vendor's prompt
    contact: str = ""  # email or phone from the seller's own vendor sheet
    source: str = "sample"  # "yours" (uploaded), "suggested" (needs sign-off) or "sample"


VENDORS: tuple[Vendor, ...] = (
    Vendor(
        "hoabinh", "Hoa Binh Garments", "Vietnam", 1.00, 0.95, 500, 35, ("OEKO-TEX",),
        "30% deposit, 70% before shipping", 0.12,
        "Friendly and balanced. Answers fully, will trade a little price for volume.",
    ),
    Vendor(
        "dongguan", "Dongguan Fast Textile", "China", 0.88, 0.84, 1000, 48, (),
        "50% deposit, 50% before shipping", 0.37,
        "Terse and cheap. Pushes for big orders. Stays vague about certifications until asked directly.",
    ),
    Vendor(
        "dhaka", "Dhaka Premium Weavers", "Bangladesh", 1.10, 1.04, 300, 60, ("WRAP", "OEKO-TEX"),
        "30% deposit, 70% before shipping", 0.12,
        "Polished and proud of quality. Firm on price. Low minimum order but the slowest.",
        cannot=("3xl", "4xl", "5xl"),
        cannot_text="any size above 2XL (so no 3XL, 4XL or 5XL); you stop at 2XL",
    ),
)


# ---------- the request ----------

def reference_cost(supplier_cost: float, changes: list[dict[str, Any]]) -> float:
    return round(supplier_cost + sum(c["unit_cost_delta"] for c in changes), 2)


def price_range(vendor: Vendor, ref: float) -> tuple[float, float]:
    """(opening price, floor) for a vendor, in dollars per unit."""
    return round(ref * vendor.open_mult, 2), round(ref * vendor.floor_mult, 2)


def declined_changes(vendor: Vendor, changes: list[dict[str, Any]]) -> list[str]:
    out = []
    for c in changes:
        text = f"{c['title']} {c.get('supplier_ask', '')}".lower()
        if any(k in text for k in vendor.cannot):
            out.append(c["title"])
    return out


def build_rfq(product: str, changes: list[dict[str, Any]], qty: int, deadline_days: int) -> str:
    lines = "\n".join(f"{i}. {c['supplier_ask']}" for i, c in enumerate(changes, 1))
    return (
        f"Hello, we are an Amazon seller looking for a supplier for: {product}.\n"
        f"We need these changes to the standard product:\n{lines}\n\n"
        f"Planned first order: {qty} units, delivered within {deadline_days} days.\n"
        "Please quote: unit price (FOB), minimum order quantity, lead time, certifications and "
        "payment terms. Please tell us clearly if you cannot make any of the changes."
    )


def buyer_target(ref: float) -> float:
    return round(ref * 0.93, 2)


# ---------- scripted conversation (no credits) ----------

def scripted_exchange(
    vendor: Vendor, ref: float, changes: list[dict[str, Any]], qty: int, deadline_days: int, product: str
) -> dict[str, Any]:
    open_price, floor = price_range(vendor, ref)
    declined = declined_changes(vendor, changes)
    certs = ", ".join(vendor.certs) if vendor.certs else "we can arrange testing on request"
    reply1 = (
        f"Thanks for the inquiry. For {product} with those changes: ${open_price:.2f} per unit FOB, "
        f"minimum order {vendor.moq} units, lead time {vendor.lead_days} days. "
        f"Certifications: {certs}. Payment: {vendor.payment}."
    )
    if declined:
        reply1 += f" Sorry, we cannot do: {', '.join(declined)}."
    target = buyer_target(ref)
    counter = (
        f"Thanks. For {qty} units we were hoping for ${target:.2f} per unit. "
        "Can you get closer to that?"
    )
    final = open_price if target >= open_price else max(floor, round(open_price - 0.6 * (open_price - target), 2))
    reply2 = (
        f"For {qty} units I can offer ${final:.2f} per unit."
        + (" That is the best I can do." if final <= floor + 0.005 else "")
    )
    return {
        "messages": [
            {"role": "buyer", "text": build_rfq(product, changes, qty, deadline_days)},
            {"role": "vendor", "text": reply1},
            {"role": "buyer", "text": counter},
            {"role": "vendor", "text": reply2},
        ],
        "quote": {
            "vendor": vendor.key,
            "unit_price": final,
            "moq": vendor.moq,
            "lead_time_days": vendor.lead_days,
            "certifications": list(vendor.certs),
            "payment_terms": vendor.payment,
            "can_make_all_changes": not declined,
            "declined_changes": declined,
            "notes": "",
        },
    }


# ---------- evaluation ----------

def evaluate(
    quotes: list[dict[str, Any]],
    base: Inputs,
    price_add: float,
    qty: int,
    deadline_days: int,
    duty_by_vendor: dict[str, float],
    vendors: "list[Vendor] | tuple[Vendor, ...] | None" = None,
) -> list[dict[str, Any]]:
    """One row per quote with landed cost, profit, margin and pass/fail flags, best first."""
    by_key = {v.key: v for v in (vendors or VENDORS)}
    rows = []
    for q in quotes:
        vendor = by_key[q["vendor"]]
        inputs = replace(
            base,
            price=base.price + price_add,
            supplier_cost=q["unit_price"],
            duty_rate=duty_by_vendor.get(vendor.key, vendor.duty_rate),
        )
        econ = compute(inputs)
        flags = {
            "moq_ok": q["moq"] <= qty,
            "lead_ok": q["lead_time_days"] <= deadline_days,
            "changes_ok": bool(q["can_make_all_changes"]),
        }
        rows.append(
            {
                **q,
                "name": vendor.name,
                "country": vendor.country,
                "duty_rate": inputs.duty_rate,
                "landed": econ["landed"],
                "profit": econ["profit"],
                "margin": econ["margin"],
                "flags": flags,
                "eligible": all(flags.values()),
            }
        )
    rows.sort(key=lambda r: (not r["eligible"], sum(not v for v in r["flags"].values()), -r["profit"]))
    return rows


# ---------- purchase order ----------

def build_po(row: dict[str, Any], product: str, changes: list[dict[str, Any]], qty: int) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc)
    return {
        "po_number": f"PO-{stamp:%Y%m%d-%H%M%S}",
        "vendor": row["name"],
        "country": row["country"],
        "product": product,
        "changes": [c["title"] for c in changes],
        "quantity": qty,
        "unit_price": row["unit_price"],
        "total": round(qty * row["unit_price"], 2),
        "payment_terms": row["payment_terms"],
        "lead_time_days": row["lead_time_days"],
        "incoterm": "FOB (assumed)",
        "status": "DRAFT: needs approval",
    }


def po_text(po: dict[str, Any]) -> str:
    changes = "\n".join(f"  - {c}" for c in po["changes"]) or "  - none"
    return (
        f"PURCHASE ORDER {po['po_number']}  [{po['status']}]\n"
        f"Vendor: {po['vendor']} ({po['country']})\n"
        f"Product: {po['product']}\n"
        f"Changes:\n{changes}\n"
        f"Quantity: {po['quantity']:,} units\n"
        f"Unit price: ${po['unit_price']:.2f} {po['incoterm']}\n"
        f"Total: ${po['total']:,.2f}\n"
        f"Payment: {po['payment_terms']}\n"
        f"Lead time: {po['lead_time_days']} days\n"
        "Ship to: [your warehouse]\n"
    )


MSG_DIR = OUTBOX / "messages"


def vendor_message(po: dict[str, Any], contact: str = "", sender: str = "[Your name]") -> str:
    """The message a person reads, edits and approves before an order goes to a supplier."""
    changes = "\n".join(f"  - {c}" for c in po["changes"]) or "  - as quoted"
    to = f"To: {po['vendor']}" + (f" <{contact}>" if contact else "")
    return (
        f"{to}\n"
        f"Subject: Purchase order {po['po_number']}: {po['product']}\n\n"
        f"Hello {po['vendor']} team,\n\n"
        "Thank you for your quote. We would like to place this order:\n\n"
        f"  Product: {po['product']}\n"
        f"  Changes:\n{changes}\n"
        f"  Quantity: {po['quantity']:,} units\n"
        f"  Unit price: ${po['unit_price']:.2f} {po['incoterm']}\n"
        f"  Total: ${po['total']:,.2f}\n"
        f"  Payment terms: {po['payment_terms']}\n"
        f"  Lead time: {po['lead_time_days']} days\n\n"
        "Please confirm the price, the lead time and that you can make every change. Please also send a "
        "pro forma invoice and the timeline for a pre-production sample.\n\n"
        f"Thank you,\n{sender}\n"
    )


def write_message(po: dict[str, Any], text: str) -> str:
    """Save an approved supplier message to data/outbox/messages/. Nothing is sent."""
    MSG_DIR.mkdir(parents=True, exist_ok=True)
    path = MSG_DIR / f"{po['po_number']}.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    path.write_text(f"Approved: {stamp} (outbox only, not sent)\n\n{text}", encoding="utf-8")
    return str(path.relative_to(MSG_DIR.parent.parent.parent)).replace("\\", "/")


def write_po(po: dict[str, Any]) -> str:
    """Write an approved PO to data/outbox/po/. Nothing is sent. Returns the repo-relative path."""
    PO_DIR.mkdir(parents=True, exist_ok=True)
    path = PO_DIR / f"{po['po_number']}.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    path.write_text(f"Approved: {stamp} (outbox only, not sent)\n\n{po_text(po)}")
    return str(path.relative_to(PO_DIR.parent.parent.parent)).replace("\\", "/")
