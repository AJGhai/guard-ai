"""Competitor comparables for MarginGuard.

Order of lookup for a category query:
  1. market/cache/<query>.json  - a saved live ZooData result (no network, no credits)
  2. live ZooData               - products/search on Amazon US, plus products/history per
                                  listing for the 90-day price change; saved to the cache
  3. market/fallback_prices.json - sample data, used when there is no key or the live call fails

Every result carries data_source: "live_zoodata" or "sample_fallback".
The key is ZOODATA_API_KEY, read from .env (or the process environment).
"""

from __future__ import annotations

import json
import os
import re
import statistics
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

LIVE_ZOODATA = "live_zoodata"
SAMPLE_FALLBACK = "sample_fallback"

ROOT = Path(__file__).resolve().parent.parent
FALLBACK = ROOT / "market" / "fallback_prices.json"
CACHE_DIR = ROOT / "market" / "cache"

API_KEY_ENV = "ZOODATA_API_KEY"
BASE_URL = "https://api.zoodata.ai/openapi/v2"
TIMEOUT = 30
PAGE_SIZE = 20  # listings requested from products/search
HISTORY_LIMIT = 10  # products/history is one call (and one credit charge) per ASIN

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

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


class ZooDataError(Exception):
    pass


# ---------- cache ----------

def _cache_path(query: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", query.strip().lower()).strip("-") or "query"
    return CACHE_DIR / f"{slug}.json"


def _read_cache(query: str) -> dict[str, Any] | None:
    path = _cache_path(query)
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if result.get("data_source") != LIVE_ZOODATA or not result.get("listings"):
        return None
    result["source_detail"] = f"cached live ZooData ({path.relative_to(ROOT)})"
    return result


def _write_cache(query: str, result: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(query).write_text(json.dumps(result, indent=2) + "\n")


# ---------- live ZooData ----------

def _post(endpoint: str, body: dict[str, Any], api_key: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{BASE_URL}/{endpoint}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "MarginGuard/1.0 (Python)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ZooDataError(f"{endpoint}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise ZooDataError(f"{endpoint}: {e}") from e
    if payload.get("success") is False:
        err = payload.get("error") or {}
        raise ZooDataError(f"{endpoint}: {err.get('code') or err.get('message') or 'failed'}")
    return payload


def _change_90d(asin: str, api_key: str) -> float | None:
    """Fractional price change over 90 days from products/history, or None if unavailable."""
    end = date.today() - timedelta(days=1)
    try:
        payload = _post("products/history", {
            "asin": asin,
            "startDate": (end - timedelta(days=90)).isoformat(),
            "endDate": end.isoformat(),
            "marketplace": "US",
        }, api_key)
    except ZooDataError:
        return None
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    prices = [p for p in ((data or {}).get("price") or []) if isinstance(p, (int, float)) and p > 0]
    if len(prices) < 2:
        return None
    return round((prices[-1] - prices[0]) / prices[0], 3)


def _live(query: str, api_key: str) -> dict[str, Any]:
    payload = _post("products/search", {
        "keyword": query,
        "sortBy": "monthlySalesFloor",
        "sortOrder": "desc",
        "pageSize": PAGE_SIZE,
        "page": 1,
    }, api_key)
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ZooDataError("products/search: unexpected response shape")

    products = [r for r in rows if isinstance(r.get("price"), (int, float)) and r["price"] > 0]
    if not products:
        raise ZooDataError("products/search: no priced listings")

    listings = []
    for i, p in enumerate(products):
        asin = p.get("asin")
        listings.append({
            "title": p.get("title") or "",
            "price": round(float(p["price"]), 2),
            "change_90d": _change_90d(asin, api_key) if asin and i < HISTORY_LIMIT else None,
            "rating": p.get("rating"),
        })

    prices = [item["price"] for item in listings]
    return {
        "category": query,
        "n": len(listings),
        "low": min(prices),
        "high": max(prices),
        "median": round(statistics.median(prices), 2),
        "listings": listings,
        "data_source": LIVE_ZOODATA,
        "source_detail": "ZooData products/search (Amazon US) + products/history",
        "fetched_at": date.today().isoformat(),
    }


# ---------- sample fallback ----------

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


def _fallback(query: str, reason: str) -> dict[str, Any]:
    data = json.loads(FALLBACK.read_text())
    categories = [k for k in data if not k.startswith("_")]
    category = _match(query, categories)
    if category is None:
        return {
            "error": "no comparables for this query",
            "available_categories": categories,
            "data_source": SAMPLE_FALLBACK,
            "fallback_reason": reason,
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
        "fallback_reason": reason,
    }


# ---------- entry point ----------

def get_comparables(params: dict[str, Any]) -> dict[str, Any]:
    query = str(params.get("category_query", "")).strip()
    if not query:
        return _fallback(query, "empty query")

    cached = _read_cache(query)
    if cached:
        return cached

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        return _fallback(query, f"{API_KEY_ENV} not set in .env")

    try:
        result = _live(query, api_key)
    except ZooDataError as e:
        return _fallback(query, f"live ZooData failed: {e}")

    _write_cache(query, result)
    return result
