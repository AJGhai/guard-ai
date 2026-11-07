"""The seller's own Amazon listing and its reviews, from ZooData, plus competitors' review themes.

    asin_from_text("https://www.amazon.com/dp/B0ABCDEFGH?th=1") -> "B0ABCDEFGH"
    get_listing(asin)              realtime/product: title, price, rating, features, image, rank
    review_themes(asins)           reviews/analysis (mode asin): issues, pain points, positives
    raw_reviews(asin, pages)       realtime/reviews: live review text (fallback when <50 reviews)
    own_reviews(asin)              themes if available, else low-star raw reviews

Every result says where it came from ("live_zoodata" or "sample"). Without ZOODATA_API_KEY, or
when a call fails, a clearly labelled sample is returned so the demo keeps working. Credits per
call: realtime/product 1, reviews/analysis per request, realtime/reviews 1 per page.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

BASE = "https://api.zoodata.ai/openapi/v2/"
TIMEOUT = 60            # same as ZooData's own CLI: realtime/product scrapes live and can be slow
ATTEMPTS = 2            # one retry on a timeout, a network error, 429 or a 5xx
THEME_LIMIT = 6

ASIN_PATTERNS = (
    r"/(?:dp|gp/product|gp/aw/d|product-reviews|exec/obidos/asin)/([A-Z0-9]{10})(?:[/?#]|$)",
    r"(?:^|[?&])asin=([A-Z0-9]{10})(?:&|$)",
    r"^\s*([A-Z0-9]{10})\s*$",
)


class ZooDataError(Exception):
    pass


def asin_from_text(text: str) -> str | None:
    """ASIN from an Amazon product link, or a bare ASIN. None if there is no ASIN."""
    t = (text or "").strip()
    for pattern in ASIN_PATTERNS:
        m = re.search(pattern, t, flags=re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None


def _key() -> str:
    key = os.environ.get("ZOODATA_API_KEY", "").strip()
    if not key:
        raise ZooDataError("No ZOODATA_API_KEY in .env")
    return key


def _post(endpoint: str, body: dict[str, Any]) -> Any:
    headers = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}
    resp = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = httpx.post(BASE + endpoint, json=body, timeout=TIMEOUT, headers=headers)
        except httpx.HTTPError as exc:
            if attempt < ATTEMPTS:
                time.sleep(2)
                continue
            raise ZooDataError(f"ZooData unreachable on {endpoint} ({type(exc).__name__})") from None
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < ATTEMPTS:
            time.sleep(2)
            continue
        break
    assert resp is not None
    if resp.status_code != 200:
        raise ZooDataError(f"ZooData HTTP {resp.status_code} on {endpoint}")
    try:
        payload = resp.json()
    except ValueError:
        raise ZooDataError("ZooData returned non-JSON") from None
    if payload.get("success") is False:
        raise ZooDataError(f"ZooData error on {endpoint}")
    return payload.get("data")


def _first(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else {}


# ---------- own listing ----------

SAMPLE_LISTING: dict[str, Any] = {
    "source": "sample",
    "asin": "SAMPLE0001",
    "title": "Women's Linen Wide-Leg Pants, High Waist, Beige",
    "brand": "Sample Brand",
    "price": 29.99,
    "rating": 3.9,
    "rating_count": 412,
    "features": ["Linen blend", "Elastic high waist", "Side pockets", "Machine washable"],
    "image": None,
    "bsr": 18450,
}


def _rank(raw: Any) -> int | None:
    if isinstance(raw, list):
        ranks = [r.get("rank") for r in raw if isinstance(r, dict) and isinstance(r.get("rank"), int)]
        return min(ranks) if ranks else None
    return raw if isinstance(raw, int) else None


def get_listing(asin: str) -> dict[str, Any]:
    """Live details of one ASIN. realtime/product can be empty on a cold start: retry once."""
    try:
        d = _first(_post("realtime/product", {"asin": asin, "marketplace": "US"}))
        if not d.get("asin") and not d.get("title"):
            time.sleep(3)
            d = _first(_post("realtime/product", {"asin": asin, "marketplace": "US"}))
        if not d.get("title"):
            raise ZooDataError("listing not found or not ready yet")
        images = d.get("images") or []
        image = images[0] if images and isinstance(images[0], str) else (
            images[0].get("link") or images[0].get("url") if images and isinstance(images[0], dict) else None
        )
        buybox = d.get("buyboxWinner") or {}
        price = buybox.get("price")
        if isinstance(price, dict):
            price = price.get("value")
        return {
            "source": "live_zoodata",
            "asin": asin,
            "title": d.get("title"),
            "brand": d.get("brand"),
            "price": float(price) if isinstance(price, (int, float)) else None,
            "rating": d.get("rating"),
            "rating_count": d.get("ratingCount"),
            "features": [str(f) for f in (d.get("features") or [])][:6],
            "image": image,
            "bsr": _rank(d.get("bestsellersRank")),
        }
    except ZooDataError as exc:
        return {**SAMPLE_LISTING, "asin": asin, "note": f"{exc}; showing a sample listing."}


# ---------- reviews ----------

SAMPLE_OWN_THEMES: dict[str, Any] = {
    "source": "sample",
    "review_count": 412,
    "avg_rating": 3.9,
    "issues": [{"element": "runs small", "rate": 0.21}, {"element": "see-through fabric", "rate": 0.14},
               {"element": "waistband loosens", "rate": 0.09}],
    "positives": [{"element": "soft and breathable", "rate": 0.31}, {"element": "flattering wide leg", "rate": 0.18}],
    "improvements": [{"element": "add a lining", "rate": 0.08}],
}
SAMPLE_COMPETITOR_THEMES: dict[str, Any] = {
    "source": "sample",
    "review_count": 5230,
    "avg_rating": 4.3,
    "issues": [{"element": "wrinkles easily", "rate": 0.12}, {"element": "runs long", "rate": 0.07}],
    "positives": [{"element": "true to size", "rate": 0.22}, {"element": "not see-through", "rate": 0.11},
                  {"element": "deep pockets", "rate": 0.10}],
    "improvements": [],
}


def _themes(insights: list[dict[str, Any]], types: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = [i for i in insights if i.get("labelType") in types and i.get("element")]
    rows.sort(key=lambda i: -(i.get("reviewRate") or 0))
    seen, out = set(), []
    for i in rows:
        key = str(i["element"]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"element": str(i["element"]), "rate": round(float(i.get("reviewRate") or 0), 3)})
    return out[:THEME_LIMIT]


def review_themes(asins: list[str], sample: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Aggregated review themes for these ASINs (one call). None if ZooData has no analysis."""
    if not asins:
        return None
    try:
        d = _first(_post("reviews/analysis", {"mode": "asin", "asins": asins, "period": "6m"}))
    except ZooDataError as exc:
        return {**sample, "note": str(exc)} if sample else None
    insights = d.get("consumerInsights") or []
    if not insights:
        return None
    return {
        "source": "live_zoodata",
        "asins": asins,
        "review_count": d.get("reviewCount"),
        "avg_rating": d.get("avgRating"),
        "issues": _themes(insights, ("issues", "painPoints")),
        "positives": _themes(insights, ("positives",)),
        "improvements": _themes(insights, ("improvements",)),
    }


def raw_reviews(asin: str, pages: int = 2) -> list[dict[str, Any]]:
    """Live review text, newest first, at most `pages` x 10 reviews (1 credit per page)."""
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(pages):
        body: dict[str, Any] = {"asin": asin, "marketplace": "US"}
        if cursor:
            body["cursor"] = cursor
        data = _first(_post("realtime/reviews", body))
        for r in data.get("reviews") or []:
            out.append({"rating": r.get("rating"), "title": str(r.get("title") or "")[:120],
                        "body": str(r.get("body") or "")[:400]})
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return out


def own_reviews(asin: str) -> dict[str, Any]:
    """The seller's review themes; if ZooData has no analysis (<50 reviews), low-star raw reviews."""
    themes = review_themes([asin], SAMPLE_OWN_THEMES)
    if themes:
        return themes
    try:
        raw = raw_reviews(asin)
    except ZooDataError as exc:
        return {**SAMPLE_OWN_THEMES, "note": str(exc)}
    low = [r for r in raw if (r.get("rating") or 5) <= 3]
    return {
        "source": "live_zoodata",
        "asins": [asin],
        "review_count": len(raw),
        "avg_rating": round(sum(r["rating"] for r in raw if r.get("rating")) / len(raw), 2) if raw else None,
        "issues": [], "positives": [], "improvements": [],
        "raw_low_star": low[:12],
        "note": "Fewer than 50 reviews: reading the latest raw reviews instead of the analysis.",
    }


def competitor_asins(listings: list[dict[str, Any]], exclude: str | None, n: int = 3) -> list[str]:
    """Top sellers with enough reviews to analyse, excluding the seller's own ASIN."""
    rows = [r for r in listings if r.get("asin") and r.get("asin") != exclude and (r.get("rating_count") or 0) >= 50]
    rows.sort(key=lambda r: -(r.get("monthly_sales_floor") or 0))
    return [r["asin"] for r in rows[:n]]
