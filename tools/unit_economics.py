"""Per-unit economics for an Amazon-style seller of finished goods.

All money is dollars per unit. Rates are fractions (0.15 = 15%).

    landed   = supplier_cost + shipping + duty          duty = duty_rate * supplier_cost
    fees     = referral_rate * price + fba_fee
    ads      = ad_rate * price
    profit   = price - landed - fees - ads
    margin   = profit / price

Duty is charged on the supplier cost (a simplification of customs value). Everything the seller
types in (fees, ad rate, duty rate) is an input, not a fact about Amazon or customs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable


@dataclass(frozen=True)
class Inputs:
    price: float
    supplier_cost: float
    shipping: float
    duty_rate: float
    referral_rate: float
    fba_fee: float
    ad_rate: float


def compute(i: Inputs) -> dict[str, Any]:
    duty = i.duty_rate * i.supplier_cost
    landed = i.supplier_cost + i.shipping + duty
    referral = i.referral_rate * i.price
    ads = i.ad_rate * i.price
    profit = i.price - landed - referral - i.fba_fee - ads
    return {
        "price": i.price,
        "parts": {
            "Supplier cost": i.supplier_cost,
            "Shipping": i.shipping,
            "Duty": duty,
            "Amazon referral fee": referral,
            "FBA fee": i.fba_fee,
            "Ads": ads,
        },
        "landed": landed,
        "profit": profit,
        "margin": profit / i.price if i.price else 0.0,
        "roi": profit / landed if landed else 0.0,
    }


# ---------- cost events (fixed presets, not sliders) ----------

EVENTS: dict[str, tuple[str, Callable[[Inputs], Inputs]]] = {
    "none": ("No change", lambda i: i),
    "duty_up": (
        "Duty on origin +25 points",
        lambda i: replace(i, duty_rate=i.duty_rate + 0.25),
    ),
    "freight_up": (
        "Ocean freight +40%",
        lambda i: replace(i, shipping=i.shipping * 1.4),
    ),
    "fba_up": (
        "FBA fee +$0.75 per unit",
        lambda i: replace(i, fba_fee=i.fba_fee + 0.75),
    ),
}


def apply_event(i: Inputs, event_key: str) -> Inputs:
    return EVENTS[event_key][1](i)


# ---------- what it would take to get the margin back ----------

def price_to_hold_margin(i: Inputs, target_margin: float) -> float | None:
    """Selling price that restores target_margin with everything else unchanged."""
    share = 1 - i.referral_rate - i.ad_rate - target_margin
    if share <= 0:
        return None
    landed = i.supplier_cost + i.shipping + i.duty_rate * i.supplier_cost
    return (landed + i.fba_fee) / share


def supplier_cost_to_hold_margin(i: Inputs, target_margin: float) -> float | None:
    """Supplier cost that restores target_margin at the current price."""
    room = i.price * (1 - i.referral_rate - i.ad_rate - target_margin) - i.fba_fee - i.shipping
    cost = room / (1 + i.duty_rate)
    return cost if cost > 0 else None


def breakeven_price_lift(i: Inputs, cost_delta: float) -> float:
    """Extra price per unit that exactly pays for an extra supplier cost (duty included)."""
    share = 1 - i.referral_rate - i.ad_rate
    return cost_delta * (1 + i.duty_rate) / share if share > 0 else float("inf")


def apply_changes(i: Inputs, cost_delta: float, price_delta: float) -> Inputs:
    """A product change: costs more to make (cost_delta), sells for more (price_delta)."""
    return replace(i, supplier_cost=i.supplier_cost + cost_delta, price=i.price + price_delta)
