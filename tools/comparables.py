"""Competitor comparables for MarginGuard.

Reads market/fallback_prices.json for now; a teammate will replace this with live ZooData.
Every result carries data_source: "live_zoodata" or "sample_fallback".
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

LIVE_ZOODATA = "live_zoodata"
SAMPLE_FALLBACK = "sample_fallback"

FALLBACK = Path(__file__).resolve().parent.parent / "market" / "fallback_prices.json"

# Map cost-sheet categories and common words onto the sample categories.
ALIASES = {
    "pants": "women linen pants",
    "pant": "women linen pants",
    "trouser": "women linen pants",
    "trousers": "women linen pants",
    "dresses": "cotton midi dress",
    "dress": "cotton midi dress",
    "jackets": "women denim jacket",
    "jacket": "women denim jacket",
}


def _match(query: str, categories: list[str]) -> str | None:
    q = query.strip().lower()
    if q in categories:
        return q
    words = set(q.replace("-", " ").split())
    for word in words:
        if word in ALIASES:
            return ALIASES[word]
    best = max(categories, key=lambda c: len(words & set(c.split())), default=None)
    if best and words & set(best.split()):
        return best
    return None


def get_comparables(params: dict[str, Any]) -> dict[str, Any]:
    data = json.loads(FALLBACK.read_text())
    categories = [k for k in data if not k.startswith("_")]
    category = _match(str(params.get("category_query", "")), categories)
    if category is None:
        return {
            "error": "no comparables for this query",
            "available_categories": categories,
            "data_source": SAMPLE_FALLBACK,
        }

    listings = data[category]["listings"]
    prices = [item["price"] for item in listings]
    return {
        "category": category,
        "n": len(listings),
        "low": min(prices),
        "high": max(prices),
        "median": statistics.median(prices),
        "listings": listings,
        "data_source": SAMPLE_FALLBACK,
        "source_detail": "market/fallback_prices.json",
    }
