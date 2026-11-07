"""Competitor comparables for MarginGuard.

Two sources, chosen by the COMPARABLES_MODE environment variable:

  fallback (default)  market/fallback_prices.json: curated premium-tier sample, no network.
  live                ZooData (Amazon): best-selling listings above COMPARABLES_PRICE_MIN
                      (default $40), de-duplicated by parent listing. Falls back to the sample
                      automatically if the key is missing, the call fails, or fewer than
                      MIN_LIVE_LISTINGS listings come back.

Every result carries data_source: "live_zoodata" or "sample_fallback", which is the source
that produced median/low/high (the numbers the pricing rules use).
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from typing import Any

import httpx

LIVE_ZOODATA = "live_zoodata"
SAMPLE_FALLBACK = "sample_fallback"

FALLBACK = Path(__file__).resolve().parent.parent / "market" / "fallback_prices.json"

ZOODATA_SEARCH_URL = "https://api.zoodata.ai/openapi/v2/products/search"
ZOODATA_FASHION_CATEGORY = "Clothing, Shoes & Jewelry"
DEFAULT_PRICE_MIN = 40.0
MIN_LIVE_LISTINGS = 5  # unique parent listings needed before live numbers are trusted
TARGET_LISTINGS = 12  # stop paging once we have this many
MAX_PAGES = 3  # 1 ZooData credit per page

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


def _stats(prices: list[float]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "n": len(prices),
        "low": min(prices),
        "high": max(prices),
        "median": statistics.median(prices),
    }
    if len(prices) >= 2:
        out["p75"] = round(statistics.quantiles(prices, n=4)[2], 2)
    return out


def _live_listings(keyword: str, price_min: float) -> list[dict[str, Any]] | None:
    """Best-selling ZooData listings at or above price_min, one per parent listing.

    Returns None when live data is unavailable or too thin, so the caller can fall back.
    """
    key = os.environ.get("ZOODATA_API_KEY", "").strip()
    if not key:
        return None

    seen: dict[str, dict[str, Any]] = {}
    try:
        with httpx.Client(timeout=15) as client:
            for page in range(1, MAX_PAGES + 1):
                resp = client.post(
                    ZOODATA_SEARCH_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "keyword": keyword,
                        "keywordMatchType": "fuzzy",
                        "categoryPath": [ZOODATA_FASHION_CATEGORY],
                        "priceMin": price_min,
                        "sortBy": "monthlySalesFloor",
                        "sortOrder": "desc",
                        "page": page,
                    },
                )
                body = resp.json()
                if resp.status_code != 200 or not body.get("success"):
                    break
                rows = body.get("data") or []
                for row in rows:
                    parent = row.get("parentAsin") or row.get("asin")
                    if parent and parent not in seen and row.get("price"):
                        seen[parent] = {
                            "title": row.get("title"),
                            "price": row["price"],
                            "brand": row.get("brandName"),
                            "rating": row.get("rating"),
                            "monthly_sales_floor": row.get("monthlySalesFloor"),
                            "bsr": row.get("bsr"),
                        }
                if len(seen) >= TARGET_LISTINGS or not rows:
                    break
    except (httpx.HTTPError, ValueError):
        pass  # keep whatever pages succeeded; the size check below decides

    listings = list(seen.values())
    return listings if len(listings) >= MIN_LIVE_LISTINGS else None


def get_comparables(params: dict[str, Any]) -> dict[str, Any]:
    query = str(params.get("category_query", ""))
    data = json.loads(FALLBACK.read_text())
    categories = [k for k in data if not k.startswith("_")]
    category = _match(query, categories)

    fallback = None
    if category is not None:
        listings = data[category]["listings"]
        fallback = {
            "category": category,
            "listings": listings,
            "data_source": SAMPLE_FALLBACK,
            "source_detail": "market/fallback_prices.json",
            **_stats([item["price"] for item in listings]),
        }

    if os.environ.get("COMPARABLES_MODE", "fallback").strip().lower() == "live":
        price_min = float(os.environ.get("COMPARABLES_PRICE_MIN", DEFAULT_PRICE_MIN))
        live = _live_listings(category or query, price_min)
        if live:
            result: dict[str, Any] = {
                "category": category or query,
                "listings": live,
                "data_source": LIVE_ZOODATA,
                "source_detail": f"ZooData Amazon best sellers, price >= ${price_min:g}",
                **_stats([item["price"] for item in live]),
            }
            if fallback:
                result["sample_median"] = fallback["median"]
            return result

    if fallback is not None:
        return fallback
    return {
        "error": "no comparables for this query",
        "available_categories": categories,
        "data_source": SAMPLE_FALLBACK,
    }
