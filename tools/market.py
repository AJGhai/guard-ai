"""Market picture for a product keyword, from ZooData (Amazon), with a clearly labelled fallback.

market_picture("women linen wide leg pants") returns price spread, estimated monthly sales,
competition and a representative listing, one entry per parent listing (variants collapsed).
Costs 1 ZooData credit per page. Without a key, or if the call fails, a static sample is returned
with source="sample" so the UI can say so.
"""

from __future__ import annotations

import os
import statistics
from typing import Any

import httpx

ZOODATA_SEARCH_URL = "https://api.zoodata.ai/openapi/v2/products/search"
FASHION_CATEGORY = "Clothing, Shoes & Jewelry"
MAX_PAGES = 2

SAMPLE = {
    "source": "sample",
    "keyword": "sample",
    "n": 12,
    "price": {"low": 18.99, "p25": 24.99, "median": 29.99, "p75": 36.99, "high": 52.0},
    "monthly_sales_floor": 41000,
    "avg_rating": 4.2,
    "median_rating_count": 1800,
    "median_sellers": 2,
    "top_image": None,
    "top_title": "Sample listing (no live data)",
    "listings": [],
}


def _quantiles(prices: list[float]) -> dict[str, float]:
    qs = statistics.quantiles(prices, n=4) if len(prices) >= 2 else [prices[0]] * 3
    return {
        "low": min(prices),
        "p25": round(qs[0], 2),
        "median": round(statistics.median(prices), 2),
        "p75": round(qs[2], 2),
        "high": max(prices),
    }


def market_picture(keyword: str) -> dict[str, Any]:
    key = os.environ.get("ZOODATA_API_KEY", "").strip()
    if not key:
        return {**SAMPLE, "keyword": keyword, "note": "No ZOODATA_API_KEY in .env"}

    seen: dict[str, dict[str, Any]] = {}
    error: str | None = None
    try:
        with httpx.Client(timeout=20) as client:
            for page in range(1, MAX_PAGES + 1):
                resp = client.post(
                    ZOODATA_SEARCH_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "keyword": keyword,
                        "keywordMatchType": "fuzzy",
                        "categoryPath": [FASHION_CATEGORY],
                        "sortBy": "monthlySalesFloor",
                        "sortOrder": "desc",
                        "page": page,
                    },
                )
                body = resp.json()
                if resp.status_code != 200 or not body.get("success"):
                    error = f"ZooData HTTP {resp.status_code}"
                    break
                for row in body.get("data") or []:
                    parent = row.get("parentAsin") or row.get("asin")
                    if parent and parent not in seen and row.get("price"):
                        seen[parent] = row
                if not body.get("data"):
                    break
    except (httpx.HTTPError, ValueError) as exc:
        error = f"{type(exc).__name__}"

    rows = list(seen.values())
    if len(rows) < 3:
        return {**SAMPLE, "keyword": keyword, "note": error or "Too few live results"}

    prices = [r["price"] for r in rows]
    sales = [r.get("parentMonthlySalesFloor") or r.get("monthlySalesFloor") or 0 for r in rows]
    ratings = [r["rating"] for r in rows if r.get("rating")]
    rating_counts = [r["ratingCount"] for r in rows if r.get("ratingCount")]
    sellers = [r["sellerCount"] for r in rows if r.get("sellerCount")]
    top = rows[0]
    return {
        "source": "live_zoodata",
        "keyword": keyword,
        "n": len(rows),
        "price": _quantiles(prices),
        "monthly_sales_floor": int(sum(sales)),
        "avg_rating": round(statistics.mean(ratings), 2) if ratings else None,
        "median_rating_count": int(statistics.median(rating_counts)) if rating_counts else None,
        "median_sellers": int(statistics.median(sellers)) if sellers else None,
        "top_image": top.get("imageUrl"),
        "top_title": top.get("title"),
        "listings": [
            {
                "title": r.get("title"),
                "brand": r.get("brandName"),
                "price": r["price"],
                "rating": r.get("rating"),
                "rating_count": r.get("ratingCount"),
                "monthly_sales_floor": r.get("parentMonthlySalesFloor") or r.get("monthlySalesFloor"),
                # for the competitor thumbnail strip (Photo to Product page)
                "image": r.get("imageUrl"),
                "bsr": r.get("bsr"),
                "asin": r.get("parentAsin") or r.get("asin"),
            }
            for r in rows
        ],
    }
