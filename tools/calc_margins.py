"""Margin calculator for MarginGuard (simple placeholder; a teammate will replace it with a tested version).

Current prices come from data/store_prices.json (the single source of truth). The CSV cost sheet
only supplies name, category, origin, unit_cost and units_per_month; its price column is ignored.

calc_margins(params) with params:
    duty_old     float  current duty rate, e.g. 0.12
    duty_new     float  scenario duty rate, e.g. 0.37
    prices       dict   {sku: price} proposed prices; SKUs not listed keep their current price
    volume_drop  float  expected unit-volume loss on repriced SKUs, e.g. 0.02 (default 0)

Per SKU:
    landed_old = unit_cost * (1 + duty_old)
    landed_new = unit_cost * (1 + duty_new)
    margin_old = (current_price - landed_old) / current_price      # baseline today
    margin_new = (new_price - landed_new) / new_price              # tariff scenario
    impact     = delta_price * units - (units * volume_drop) * (new_price - landed_new)
                 for repriced SKUs, otherwise 0

Totals:
    exposure       = sum(units * (landed_new - landed_old))   monthly cost increase from the duty change
    recovery       = sum(impact)                              monthly profit recovered by repricing
    blended_margin = sum(units' * (price - landed_new)) / sum(units' * price),
                     where units' = units * (1 - volume_drop) on repriced SKUs
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data"
COST_SHEET = DATA / "sample_merchant_data.csv"
STORE_PRICES = DATA / "store_prices.json"


def load_cost_sheet(path: Path = COST_SHEET) -> list[dict[str, Any]]:
    """Cost sheet rows with `price` taken from data/store_prices.json."""
    prices = json.loads(STORE_PRICES.read_text())
    with path.open(newline="") as f:
        rows = []
        for row in csv.DictReader(f):
            sku = row["sku"]
            if sku not in prices:
                raise ValueError(f"{sku} is missing from {STORE_PRICES.name}")
            rows.append(
                {
                    "sku": sku,
                    "name": row["name"],
                    "category": row["category"],
                    "origin": row["origin"],
                    "unit_cost": float(row["unit_cost"]),
                    "price": float(prices[sku]),
                    "units_per_month": int(row["units_per_month"]),
                }
            )
        return rows


def calc_margins(params: dict[str, Any]) -> dict[str, Any]:
    duty_old = float(params["duty_old"])
    duty_new = float(params["duty_new"])
    prices = {str(k): float(v) for k, v in (params.get("prices") or {}).items()}
    volume_drop = float(params.get("volume_drop") or 0.0)

    rows = load_cost_sheet()
    known = {r["sku"] for r in rows}
    unknown = sorted(set(prices) - known)
    if unknown:
        raise ValueError(f"Unknown SKUs in prices: {', '.join(unknown)}")

    skus: dict[str, Any] = {}
    exposure = recovery = profit = revenue = 0.0
    for r in rows:
        cost, units, current = r["unit_cost"], r["units_per_month"], r["price"]
        new_price = prices.get(r["sku"], current)
        if new_price <= 0:
            raise ValueError(f"Price for {r['sku']} must be positive")
        repriced = new_price != current

        landed_old = cost * (1 + duty_old)
        landed_new = cost * (1 + duty_new)
        margin_old = (current - landed_old) / current
        margin_new = (new_price - landed_new) / new_price
        impact = (
            (new_price - current) * units - (units * volume_drop) * (new_price - landed_new)
            if repriced
            else 0.0
        )

        units_after = units * (1 - volume_drop) if repriced else units
        exposure += units * (landed_new - landed_old)
        recovery += impact
        profit += units_after * (new_price - landed_new)
        revenue += units_after * new_price

        skus[r["sku"]] = {
            "price_old": round(current, 2),
            "price_new": round(new_price, 2),
            "landed_old": round(landed_old, 2),
            "landed_new": round(landed_new, 2),
            "margin_old": round(margin_old, 4),
            "margin_new": round(margin_new, 4),
            "impact": round(impact, 2),
        }

    return {
        "skus": skus,
        "totals": {
            "exposure": round(exposure, 2),
            "recovery": round(recovery, 2),
            "blended_margin": round(profit / revenue, 4) if revenue else 0.0,
        },
        "assumptions": {
            "duty_old": duty_old,
            "duty_new": duty_new,
            "volume_drop": volume_drop,
            "period": "per month",
        },
    }
