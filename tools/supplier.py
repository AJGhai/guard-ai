"""Supplier tools for SupplierNegotiator.

calc_target_cost(params, base_prices=None) with params:
    sku            str    a SKU from the cost sheet
    target_margin  float  margin to restore after the duty change, default 0.60
    duty_old       float  current duty rate, default 0.12
    duty_new       float  scenario duty rate, default 0.37

    target_cost   = price * (1 - target_margin) / (1 + duty_new)
    reduction_pct = (target_cost - current_cost) / current_cost * 100   (negative = cheaper)

send_supplier_emails writes approved drafts to data/outbox/<sku>.md. Nothing is ever sent:
the outbox is the only output, and the address always comes from data/suppliers.csv.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.calc_margins import load_cost_sheet

DATA = Path(__file__).resolve().parent.parent / "data"
SUPPLIERS = DATA / "suppliers.csv"
OUTBOX = DATA / "outbox"


def load_suppliers() -> dict[str, dict[str, str]]:
    with SUPPLIERS.open(newline="") as f:
        return {row["sku"]: row for row in csv.DictReader(f)}


def calc_target_cost(
    params: dict[str, Any], base_prices: dict[str, float] | None = None
) -> dict[str, Any]:
    sku = str(params["sku"])
    target_margin = float(params.get("target_margin", 0.60))
    duty_old = float(params.get("duty_old", 0.12))
    duty_new = float(params.get("duty_new", 0.37))
    if not 0 < target_margin < 1:
        raise ValueError("target_margin must be between 0 and 1")

    rows = {r["sku"]: r for r in load_cost_sheet(base_prices=base_prices)}
    if sku not in rows:
        raise ValueError(f"Unknown SKU {sku}")
    row = rows[sku]
    supplier = load_suppliers().get(sku)
    if supplier is None:
        raise ValueError(f"No supplier on file for {sku}")

    current_cost = row["unit_cost"]
    target_cost = row["price"] * (1 - target_margin) / (1 + duty_new)
    return {
        "sku": sku,
        "name": row["name"],
        "category": row["category"],
        "price": row["price"],
        "units_per_month": row["units_per_month"],
        "supplier": supplier["supplier"],
        "contact_email": supplier["contact_email"],
        "duty_old": duty_old,
        "duty_new": duty_new,
        "target_margin": target_margin,
        "current_cost": round(current_cost, 2),
        "target_cost": round(target_cost, 2),
        "reduction_per_unit": round(current_cost - target_cost, 2),
        "reduction_pct": round((target_cost - current_cost) / current_cost * 100, 1),
    }


def describe_emails(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drafts as shown to a reviewer, addressed from data/suppliers.csv."""
    suppliers = load_suppliers()
    described = []
    for e in emails:
        sku = str(e.get("sku"))
        s = suppliers.get(sku, {})
        described.append({
            "sku": sku,
            "supplier": s.get("supplier"),
            "to": s.get("contact_email"),
            "subject": str(e.get("subject", "")),
            "body": str(e.get("body", "")),
        })
    return described


def send_supplier_emails(
    emails: list[dict[str, Any]], decisions: dict[str, bool]
) -> dict[str, Any]:
    """Write approved drafts to data/outbox/<sku>.md. No email is sent."""
    written, rejected = [], []
    for e in describe_emails(emails):
        sku = e["sku"]
        if not decisions.get(sku, False):
            rejected.append({"sku": sku, "status": "rejected by reviewer"})
            continue
        if not e["to"]:
            rejected.append({"sku": sku, "status": "no supplier on file"})
            continue
        OUTBOX.mkdir(parents=True, exist_ok=True)
        path = OUTBOX / f"{sku}.md"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        path.write_text(
            f"To: {e['supplier']} <{e['to']}>\n"
            f"Subject: {e['subject']}\n"
            f"Approved: {stamp} (outbox only, not sent)\n\n"
            f"{e['body'].strip()}\n"
        )
        written.append({"sku": sku, "supplier": e["supplier"], "to": e["to"],
                        "path": str(path.relative_to(DATA.parent))})
    return {"written": written, "rejected": rejected, "sent": False}


def clear_outbox() -> None:
    for path in OUTBOX.glob("*.md"):
        path.unlink()


def read_outbox() -> list[dict[str, str]]:
    return [
        {"sku": p.stem, "path": str(p.relative_to(DATA.parent)), "content": p.read_text()}
        for p in sorted(OUTBOX.glob("*.md"))
    ]
