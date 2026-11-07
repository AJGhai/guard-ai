"""Customer-review evidence for Product-to-PO, from ZooData (reviews/search, realtime/product).

Why this exists: recommendations should come from what real buyers wrote, not from a model's
guess. This module fetches the most helpful low-star and high-star reviews of the competing
listings (with ZooData's AI tags), turns them into counted themes, and gives every review a
short id (R1, R2, ...). The advisor agent may only cite theme names and review ids that exist
here, and every count shown to a person is computed here, never by the model.

Fetched data is cached on disk (market/cache/reviews, market/cache/listings) so the demo is fast
and works offline for products already fetched. ZooData allows 5 concurrent requests; we use 3.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
REVIEW_CACHE = ROOT / "market" / "cache" / "reviews"
LISTING_CACHE = ROOT / "market" / "cache" / "listings"
BASE = "https://api.zoodata.ai/openapi/v2"

LOW_MAX_RATING = 3
LOW_PAGE = 20
HIGH_PAGE = 10
QUOTE_CHARS = 320
PROMPT_REVIEWS = 24  # how many low-star reviews the agent gets to read in full

THEME_TYPES = {
    "mentioned_issues": "issue",
    "mentioned_improvements": "improvement",
    "mentioned_pain_points": "pain point",
}

ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/product/|asin=)([A-Z0-9]{10})", re.I)


# ---------- ZooData calls ----------

def _post(path: str, body: dict[str, Any], timeout: int = 90, retries: int = 2) -> dict[str, Any]:
    key = os.environ.get("ZOODATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("No ZOODATA_API_KEY in .env")
    for attempt in range(retries + 1):
        resp = httpx.post(f"{BASE}/{path}", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict) and err.get("code") == "RATE_LIMIT_EXCEEDED" and attempt < retries:
            time.sleep(1.5 * (attempt + 1))
            continue
        if not data.get("success"):
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"ZooData {path}: {msg or resp.status_code}")
        return data
    raise RuntimeError("ZooData: too many retries")


def parse_asin(text: str) -> str | None:
    """An ASIN from an Amazon URL or a bare 10-character id."""
    text = (text or "").strip()
    m = ASIN_RE.search(text)
    if m:
        return m.group(1).upper()
    if re.fullmatch(r"[A-Za-z0-9]{10}", text) and any(c.isdigit() for c in text):
        return text.upper()
    return None


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _price_value(price: Any) -> float | None:
    """ZooData returns price as {'value': 24.99, 'currency': 'USD', ...}; accept a bare number too."""
    if isinstance(price, dict):
        price = price.get("value")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def fetch_listing(asin: str, refresh: bool = False) -> dict[str, Any]:
    """Listing details for one ASIN (title, features, images, rating). Cached."""
    path = LISTING_CACHE / f"{asin}.json"
    if not refresh and (cached := _read(path)):
        return {**cached, "price": _price_value(cached.get("price"))}
    d = _post("realtime/product", {"asin": asin}).get("data") or {}
    if not d.get("title"):
        raise RuntimeError(f"No listing found for {asin}")
    buybox = d.get("buyboxWinner") or {}
    listing = {
        "asin": d.get("asin") or asin,
        "parent_asin": d.get("parentAsin"),
        "title": d.get("title"),
        "brand": d.get("brandName"),
        "price": _price_value(buybox.get("price")),
        "rating": d.get("rating"),
        "rating_count": d.get("ratingCount"),
        "rating_breakdown": d.get("ratingBreakdown"),
        "features": [str(f) for f in (d.get("features") or [])][:8],
        "images": (d.get("images") or [])[:8],
        "image": d.get("imageUrl") or ((d.get("images") or [None])[0]),
        "category_path": d.get("categoryPath"),
        "link": d.get("link"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write(path, listing)
    return listing


def _slim(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": review.get("title") or "",
        "body": review.get("body") or "",
        "rating": review.get("rating"),
        "helpful": review.get("helpfulVoteCount") or 0,
        "verified": bool(review.get("verifiedPurchase")),
        "date": review.get("date"),
        "summary": review.get("reviewSummary"),
        "tags": [[t.get("labelType"), t.get("element")] for t in (review.get("tags") or [])],
    }


def fetch_reviews(asin: str, refresh: bool = False) -> dict[str, Any]:
    """Most helpful low-star and high-star reviews for one ASIN, with tags. Cached."""
    path = REVIEW_CACHE / f"{asin}.json"
    if not refresh and (cached := _read(path)):
        return cached
    low = _post(
        "reviews/search",
        {"asin": asin, "ratingMax": LOW_MAX_RATING, "sortBy": "helpfulVoteCount", "sortOrder": "desc", "pageSize": LOW_PAGE},
    ).get("data") or []
    high = _post(
        "reviews/search",
        {"asin": asin, "ratingMin": 4, "sortBy": "helpfulVoteCount", "sortOrder": "desc", "pageSize": HIGH_PAGE},
    ).get("data") or []
    result = {
        "asin": asin,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "low": [_slim(r) for r in low],
        "high": [_slim(r) for r in high],
    }
    _write(path, result)
    return result


def gather_reviews(asins: list[str], refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Reviews for several ASINs in parallel. A failed ASIN maps to {"error": "..."}."""

    def one(asin: str) -> tuple[str, dict[str, Any]]:
        try:
            return asin, fetch_reviews(asin, refresh)
        except Exception as exc:  # report per ASIN, keep the others
            return asin, {"asin": asin, "low": [], "high": [], "error": f"{type(exc).__name__}: {exc}"[:200]}

    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(pool.map(one, asins))


# ---------- themes and evidence ----------

def _themes_of(review: dict[str, Any]) -> list[tuple[str, str]]:
    """(kind, theme) pairs for one review. ZooData joins several labels with commas."""
    out: list[tuple[str, str]] = []
    for label_type, element in review.get("tags") or []:
        kind = THEME_TYPES.get(label_type)
        if not kind or not element:
            continue
        for part in str(element).split(","):
            name = part.strip()
            if name:
                out.append((kind, name))
    return out


def _text(review: dict[str, Any]) -> str:
    title, body = review["title"].strip(), review["body"].strip()
    text = f"{title}. {body}" if title and body else (title or body)
    return re.sub(r"\s+", " ", text)


def build_evidence(
    competitors: list[dict[str, Any]], reviews_by_asin: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Counted themes plus citable reviews, from the fetched reviews.

    competitors: [{"asin", "name", "role"}]. Returns themes (name, kind, count, share, avg rating,
    review ids), positives, and reviews keyed by id (R1, R2, ... by helpfulness).
    """
    names = {c["asin"]: c.get("name") or c["asin"] for c in competitors}
    low_rows: list[tuple[str, dict[str, Any]]] = []
    high_rows: list[tuple[str, dict[str, Any]]] = []
    sources = []
    for c in competitors:
        data = reviews_by_asin.get(c["asin"]) or {}
        low_rows += [(c["asin"], r) for r in data.get("low", [])]
        high_rows += [(c["asin"], r) for r in data.get("high", [])]
        sources.append(
            {"asin": c["asin"], "name": names[c["asin"]], "role": c.get("role", "competitor"),
             "n_low": len(data.get("low", [])), "n_high": len(data.get("high", [])), "error": data.get("error")}
        )
    low_rows.sort(key=lambda x: (-(x[1]["helpful"] or 0), x[1]["rating"] or 5))

    reviews: dict[str, dict[str, Any]] = {}
    theme_reviews: dict[tuple[str, str], list[str]] = defaultdict(list)
    display: dict[tuple[str, str], str] = {}
    for i, (asin, r) in enumerate(low_rows, 1):
        rid = f"R{i}"
        themes = _themes_of(r)
        reviews[rid] = {
            "id": rid, "asin": asin, "competitor": names[asin], "rating": r["rating"], "helpful": r["helpful"],
            "verified": r["verified"], "text": _text(r)[:QUOTE_CHARS], "themes": sorted({t for _, t in themes}),
        }
        seen = set()
        for kind, name in themes:
            key = (kind, name.lower())
            display.setdefault(key, name)
            if key not in seen:
                seen.add(key)
                theme_reviews[key].append(rid)

    n_low = len(low_rows)
    themes_out = []
    for key, ids in theme_reviews.items():
        ratings = [reviews[i]["rating"] for i in ids if reviews[i]["rating"]]
        themes_out.append(
            {"name": display[key], "kind": key[0], "count": len(ids), "share": len(ids) / n_low if n_low else 0.0,
             "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None, "review_ids": ids}
        )
    themes_out.sort(key=lambda t: (-t["count"], t["name"]))

    pos: dict[str, int] = defaultdict(int)
    for _, r in high_rows:
        for label_type, element in r.get("tags") or []:
            if label_type == "mentioned_positives" and element:
                for part in str(element).split(","):
                    if part.strip():
                        pos[part.strip().title()] += 1
    positives = [{"name": n, "count": c} for n, c in sorted(pos.items(), key=lambda kv: -kv[1])[:8]]

    return {
        "n_low": n_low, "n_high": len(high_rows), "sources": sources,
        "themes": themes_out[:14], "positives": positives, "reviews": reviews,
    }


def support(evidence: dict[str, Any], themes: list[str], review_ids: list[str]) -> dict[str, Any]:
    """How many low-star reviews back a set of themes/review ids, counted from the data."""
    wanted = {t.lower() for t in themes}
    ids = set(review_ids)
    for rid, r in evidence["reviews"].items():
        if wanted & {t.lower() for t in r["themes"]}:
            ids.add(rid)
    ids = {i for i in ids if i in evidence["reviews"]}
    ratings = [evidence["reviews"][i]["rating"] for i in ids if evidence["reviews"][i]["rating"]]
    n = evidence["n_low"]
    return {
        "count": len(ids),
        "of": n,
        "share": len(ids) / n if n else 0.0,
        "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "review_ids": sorted(ids, key=lambda x: int(x[1:])),
    }


# ---------- prompt for the agent ----------

def prompt_pack(evidence: dict[str, Any]) -> dict[str, Any]:
    """The part of the evidence the agent reads: counted themes and the most helpful reviews."""
    top_ids = list(evidence["reviews"])[:PROMPT_REVIEWS]
    return {
        "sample": f"{evidence['n_low']} low-star (1-3) reviews across {len(evidence['sources'])} listings",
        "themes": [
            {"name": t["name"], "kind": t["kind"], "reviews": t["count"], "share_of_low_star": round(t["share"], 2)}
            for t in evidence["themes"]
        ],
        "what_buyers_like": [p["name"] for p in evidence["positives"]],
        "reviews": [
            {k: evidence["reviews"][i][k] for k in ("id", "rating", "helpful", "text", "themes")} for i in top_ids
        ],
    }


# ---------- rule-based fallback (same evidence, no agent) ----------

RULES: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (("shrink",), dict(title="Pre-washed, shrink-resistant fabric", region="whole", unit_cost_delta=0.45, price_delta=1.0,
        supplier_ask="Can you supply pre-washed fabric with under 3% shrinkage after washing, and quote the extra cost?")),
    (("see-through", "sheer", "thin"), dict(title="Add a lining or use a heavier fabric", region="lower_body", unit_cost_delta=0.85, price_delta=2.0,
        supplier_ask="Can you add a breathable lining, or use a heavier fabric weight, and quote the extra cost?")),
    (("fit", "sizing", "size", "large", "small", "tight", "short length"), dict(title="Corrected size chart and graded patterns", region="waist", unit_cost_delta=0.20, price_delta=0.0,
        supplier_ask="Please send a corrected size chart with measurements per size and a fit sample in M and XL.")),
    (("material quality", "quality", "rough", "texture", "cheap", "thick"), dict(title="Upgrade fabric quality", region="whole", unit_cost_delta=0.90, price_delta=2.0,
        supplier_ask="Can you quote a denser, softer fabric option and its extra cost per unit?")),
    (("color", "colour", "misleading", "description", "picture", "photo"), dict(title="Color-matched fabric lot and accurate listing photos", region="whole", unit_cost_delta=0.15, price_delta=0.0,
        supplier_ask="Can you confirm the fabric color matches our approved swatch for every production lot?")),
]


def rule_based(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Up to three improvements picked by which themes are most common. Labelled as rule-based."""
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for words, tpl in RULES:
        matched = [t for t in evidence["themes"] if any(w in t["name"].lower() for w in words)]
        if not matched or tpl["title"] in used:
            continue
        used.add(tpl["title"])
        out.append(
            {**tpl, "observation": "Chosen by rule from the most common review themes.",
             "themes": [t["name"] for t in matched[:4]], "review_ids": [], "confidence": "medium",
             "risk": "Estimate only; confirm cost and effect with the supplier."}
        )
    out.sort(key=lambda i: -support(evidence, i["themes"], [])["count"])
    return out[:3]
