"""MarginGuard decision rules, applied deterministically per SKU.

Order, using the tariff-scenario margin at the current price (calc_margins margin_new):
  a) REVIEW  if tariff margin < 56%. Not repriced; flagged for sourcing review.
  b) REPRICE if current price < comparable median.
             new price = min(median, round(current_price * 1.10)) in whole dollars,
             only if the increase is at least $2; otherwise ABSORB.
  c) ABSORB  otherwise.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tools.calc_margins import calc_margins, load_cost_sheet

REVIEW_MARGIN = 0.56
MARKUP_CAP = 1.10
MIN_INCREASE = 2


def _num(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _round_dollars(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def evaluate_pricing_rules(params: dict[str, Any]) -> dict[str, Any]:
    """params: duty_old, duty_new, medians {cost-sheet category: comparable median}."""
    medians = {str(k).lower(): float(v) for k, v in (params.get("medians") or {}).items()}
    rows = load_cost_sheet()
    missing = sorted({r["category"] for r in rows if r["category"].lower() not in medians})
    if missing:
        raise ValueError(f"Missing medians for categories: {', '.join(missing)}")

    margins = calc_margins(
        {"duty_old": params["duty_old"], "duty_new": params["duty_new"], "prices": {}, "volume_drop": 0}
    )["skus"]

    decisions = []
    for r in rows:
        sku, price = r["sku"], r["price"]
        median = medians[r["category"].lower()]
        tariff_margin = margins[sku]["margin_new"]
        new_price = price

        if tariff_margin < REVIEW_MARGIN:
            action = "REVIEW"
            reason = (
                f"Tariff margin {tariff_margin:.1%} is below {REVIEW_MARGIN:.0%}; "
                "flag for sourcing review, do not reprice."
            )
        elif price < median:
            candidate = min(median, _round_dollars(price * MARKUP_CAP))
            if candidate - price >= MIN_INCREASE:
                action, new_price = "REPRICE", candidate
                capped_by = "competitor median" if candidate == median else "10% cap"
                reason = (
                    f"Below competitor median ${median:g}; raise to ${candidate:g} "
                    f"(limited by {capped_by})."
                )
            else:
                action = "ABSORB"
                reason = f"Below median ${median:g}, but the allowed increase is under ${MIN_INCREASE}."
        else:
            action = "ABSORB"
            reason = f"At or above competitor median ${median:g}."

        decisions.append(
            {
                "sku": sku,
                "name": r["name"],
                "category": r["category"],
                "action": action,
                "current_price": _num(price),
                "new_price": _num(new_price),
                "median": _num(median),
                "tariff_margin": tariff_margin,
                "reason": reason,
            }
        )

    return {
        "decisions": decisions,
        "reprice_prices": {d["sku"]: d["new_price"] for d in decisions if d["action"] == "REPRICE"},
        "rules": {
            "review_below_margin": REVIEW_MARGIN,
            "markup_cap": MARKUP_CAP,
            "min_increase": MIN_INCREASE,
        },
    }
