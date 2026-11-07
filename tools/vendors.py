"""The seller's own vendors, from a spreadsheet (CSV or Excel).

parse_vendor_sheet(data, filename, base_cost) -> (vendors, warnings)

Columns (case and spacing do not matter; these names or close ones):
    name (required), country, contact (email or phone), unit_price (what they charge you today for the
    product, required), moq, lead_time_days, certifications (comma separated), payment_terms,
    duty_rate (percent), notes

Their real terms become the simulated vendor's private facts: the opening price is their unit_price
(plus the extra cost of the changes, scaled), the floor is 5% lower, and MOQ, lead time, certifications
and payment terms come from the row. Nobody is contacted: replies are simulated from these terms.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pandas as pd

from tools.rfq import Vendor

MAX_VENDORS = 5

ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "vendor", "vendor name", "supplier", "supplier name", "company"),
    "country": ("country", "origin", "country of origin", "location"),
    "contact": ("contact", "email", "e-mail", "phone", "whatsapp", "contact email", "contact phone"),
    "unit_price": ("unit price", "unit_price", "price", "fob", "fob price", "price per unit", "cost", "unit cost"),
    "moq": ("moq", "min order", "minimum order", "minimum order quantity", "min order qty"),
    "lead_time_days": ("lead time", "lead time days", "lead_time_days", "lead time (days)", "days"),
    "certifications": ("certifications", "certification", "certs", "certificates"),
    "payment_terms": ("payment terms", "payment_terms", "payment", "terms"),
    "duty_rate": ("duty", "duty rate", "duty_rate", "duty %", "duty rate %"),
    "notes": ("notes", "note", "comments", "about"),
}

TEMPLATE_CSV = (
    "name,country,contact,unit_price,moq,lead_time_days,certifications,payment_terms,duty_rate,notes\n"
    "Sunrise Apparel Co,Vietnam,sales@sunrise-apparel.example.com,9.40,500,38,OEKO-TEX,30% deposit; 70% before shipping,12,"
    "Reliable and polite. Will trade a little price for volume.\n"
    "Lotus Textile Works,India,+91 98000 00000,8.90,800,45,GOTS,50% deposit; 50% before shipping,12,"
    "Cheaper but slower to reply.\n"
)


SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "vendor_sets"

# Pre-loaded vendor sheets for the demo. Every vendor in them is fictional.
SAMPLE_SETS: dict[str, str] = {
    "Vietnam and India apparel vendors": "vietnam_india.csv",
    "Budget China vendors": "budget_china.csv",
    "Premium certified vendors": "premium_certified.csv",
    "Small-batch mixed vendors": "small_batch_mixed.csv",
}


def load_sample_set(label: str, base_cost: float) -> tuple[list[Vendor], list[str]]:
    filename = SAMPLE_SETS[label]
    return parse_vendor_sheet((SAMPLE_DIR / filename).read_bytes(), filename, base_cost)


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", str(text).lower()).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "vendor"


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(value))
    return float(m.group(0).replace(",", "")) if m else None


def _read(data: bytes, filename: str) -> pd.DataFrame:
    if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(data))
    return pd.read_csv(io.BytesIO(data), encoding_errors="replace")


def parse_vendor_sheet(data: bytes, filename: str, base_cost: float) -> tuple[list[Vendor], list[str]]:
    """Vendors from the sheet, plus plain-language warnings about rows that were skipped or defaulted."""
    warnings: list[str] = []
    df = _read(data, filename)
    lookup = {_norm(c): c for c in df.columns}

    def col(field: str) -> str | None:
        for alias in ALIASES[field]:
            if _norm(alias) in lookup:
                return lookup[_norm(alias)]
        return None

    name_col, price_col = col("name"), col("unit_price")
    if name_col is None or price_col is None:
        raise ValueError("The sheet needs at least a 'name' column and a 'unit_price' column.")

    vendors: list[Vendor] = []
    seen: set[str] = set()
    for i, row in df.iterrows():
        label = f"Row {i + 2}"
        name = str(row[name_col]).strip() if not pd.isna(row[name_col]) else ""
        price = _num(row[price_col])
        if not name or name.lower() == "nan":
            continue
        if price is None or price <= 0:
            warnings.append(f"{label} ({name}): no valid unit price, skipped.")
            continue
        if len(vendors) >= MAX_VENDORS:
            warnings.append(f"Only the first {MAX_VENDORS} vendors are used; {name} and later rows were skipped.")
            break

        def get(field: str) -> Any:
            c = col(field)
            return row[c] if c is not None else None

        moq = _num(get("moq"))
        lead = _num(get("lead_time_days"))
        duty = _num(get("duty_rate"))
        if moq is None:
            warnings.append(f"{label} ({name}): no minimum order, assuming 500.")
        if lead is None:
            warnings.append(f"{label} ({name}): no lead time, assuming 45 days.")
        certs_raw = get("certifications")
        certs = tuple(c.strip() for c in str(certs_raw).split(",") if c.strip() and c.strip().lower() != "nan") \
            if certs_raw is not None and not pd.isna(certs_raw) else ()
        pay_raw = get("payment_terms")
        notes_raw = get("notes")
        country_raw = get("country")
        contact_raw = get("contact")
        key = f"my_{_slug(name)}"
        if key in seen:
            key = f"{key}_{len(seen)}"
        seen.add(key)
        open_mult = price / base_cost if base_cost > 0 else 1.0
        vendors.append(
            Vendor(
                key=key,
                name=name,
                country=str(country_raw).strip() if country_raw is not None and not pd.isna(country_raw) else "n/a",
                open_mult=open_mult,
                floor_mult=open_mult * 0.95,
                moq=int(moq) if moq else 500,
                lead_days=int(lead) if lead else 45,
                certs=certs,
                payment=str(pay_raw).strip() if pay_raw is not None and not pd.isna(pay_raw) else "30% deposit, 70% before shipping",
                duty_rate=(duty / 100) if duty is not None else 0.12,
                personality=(str(notes_raw).strip() if notes_raw is not None and not pd.isna(notes_raw) and str(notes_raw).strip()
                             else "A supplier the buyer already works with. Professional and willing to keep the relationship."),
                contact=str(contact_raw).strip() if contact_raw is not None and not pd.isna(contact_raw) else "",
                source="yours",
            )
        )
    if not vendors:
        raise ValueError("No usable vendor rows found. Each row needs a name and a unit price.")
    return vendors, warnings
