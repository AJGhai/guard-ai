"""Product-improvement pipeline: listing / photo / description -> reviews -> market -> comparison.

run_pipeline(...) runs every analysis step in order and reports each one through on_step, so the
page can show a live progress list (and autopilot can reuse it). Each step falls back to a
labelled sample on its own, so one failing call never stops the run.

    1. Read the listing (link mode): ZooData realtime/product, plus its main photo
    2. Read the seller's reviews: ZooData reviews/analysis, or raw reviews when there are few
    3. Agent looks at the product (photo tool) and the reviews -> spec, weak points, improvements
    4. Search the market with the agent's terms: ZooData products/search
    5. Read competitors' reviews: ZooData reviews/analysis on the top sellers
    6. Agent compares the seller with competitors -> doing well / room to improve
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx

from agent.advisor import advise_evidence
from agent.photo_spec import analyze_photo, compare_with_competitors, prepare_image
from tools.reviews import build_evidence, gather_reviews
from tools.listing import (
    SAMPLE_COMPETITOR_THEMES,
    asin_from_text,
    competitor_asins,
    get_listing,
    own_reviews,
    review_themes,
)
from tools.market import market_picture

Step = Callable[[str], None]


def fetch_image(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        return prepare_image(resp.content)
    except Exception:
        return None


def _you(listing: dict[str, Any] | None, own: dict[str, Any] | None, price: float) -> dict[str, Any]:
    listing = listing or {}
    own = own or {}
    return {
        "price": listing.get("price") or price,
        "rating": listing.get("rating") or own.get("avg_rating"),
        "rating_count": listing.get("rating_count") or own.get("review_count"),
        "best_seller_rank": listing.get("bsr"),
        "own_issues": own.get("issues") or [],
        "own_positives": own.get("positives") or [],
        "own_low_star_reviews": [r["body"] for r in own.get("raw_low_star") or []][:8],
    }


def _competitors(market: dict[str, Any], themes: dict[str, Any] | None) -> dict[str, Any]:
    p = market.get("price") or {}
    counts = sorted(r.get("rating_count") or 0 for r in market.get("listings") or [])
    return {
        "listings": market.get("n"),
        "price_median": p.get("median"),
        "price_middle_half": [p.get("p25"), p.get("p75")],
        "avg_rating": market.get("avg_rating"),
        "median_rating_count": counts[len(counts) // 2] if counts else market.get("median_rating_count"),
        "review_themes": {k: (themes or {}).get(k) for k in ("issues", "positives")} if themes else None,
    }


MIN_LOW_STAR_FOR_EVIDENCE = 5


def evidence_competitors(listing: dict[str, Any] | None, market: dict[str, Any]) -> list[dict[str, Any]]:
    """Up to three listings to read reviews for: the seller's own (if known), then top sellers."""
    out: list[dict[str, Any]] = []
    skip: set[str] = set()
    if listing and listing.get("asin"):
        out.append({"asin": listing["asin"], "name": f"Your listing ({listing['asin']})", "role": "your listing"})
        skip |= {listing["asin"], listing.get("parent_asin") or ""}
    for item in sorted(market.get("listings") or [], key=lambda r: -(r.get("monthly_sales_floor") or 0)):
        if not item.get("asin") or item["asin"] in skip or item.get("parent_asin") in skip:
            continue
        out.append({"asin": item["asin"], "name": item.get("brand") or (item.get("title") or "")[:28], "role": "competitor"})
        if len(out) >= 3:
            break
    return out


def themes_from_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Competitor review themes in the shape the comparison agent expects, from the evidence."""
    high = max(evidence["n_high"], 1)

    def top(kinds: tuple[str, ...]) -> list[dict[str, Any]]:
        return [{"element": t["name"], "rate": round(t["share"], 3)} for t in evidence["themes"] if t["kind"] in kinds][:6]

    return {
        "source": "live_zoodata",
        "asins": [s["asin"] for s in evidence["sources"]],
        "review_count": evidence["n_low"] + evidence["n_high"],
        "avg_rating": None,
        "issues": top(("issue", "pain point")),
        "positives": [{"element": p["name"], "rate": round(p["count"] / high, 3)} for p in evidence["positives"][:6]],
        "improvements": top(("improvement",)),
        "basis": "most helpful reviews; issue rates are the share of low-star reviews",
    }


def grounded_to_spec(grounded: dict[str, Any]) -> list[dict[str, Any]]:
    """Advisor changes in the page's improvement shape, keeping the evidence fields."""
    return [
        {**item, "fixes": item.get("observation") or item["title"]}
        for item in grounded["improvements"]
    ]


def run_pipeline(
    mode: str,
    *,
    link: str = "",
    photo: bytes | None = None,
    description: str = "",
    complaints: list[str] | None = None,
    price: float = 29.99,
    on_step: Step = lambda _s: None,
) -> dict[str, Any]:
    """mode is "link", "photo" or "description". Returns everything the page needs."""
    state: dict[str, Any] = {"mode": mode, "listing": None, "own_reviews": None, "steps": []}

    def step(label: str) -> None:
        state["steps"].append(label)
        on_step(label)

    context: dict[str, Any] = {}
    jpeg = photo
    if mode == "link":
        asin = asin_from_text(link)
        if not asin:
            raise ValueError("That link has no Amazon product ID (ASIN). Paste the product page link.")
        step(f"Reading your listing {asin} on ZooData")
        listing = get_listing(asin)
        state["listing"] = listing
        jpeg = fetch_image(listing.get("image"))
        step("Reading what your buyers say in reviews")
        own = own_reviews(asin)
        state["own_reviews"] = own
        context = {
            "listing": {k: listing.get(k) for k in ("title", "features", "price", "rating", "rating_count")},
            "own_review_themes": {k: own.get(k) for k in ("issues", "positives", "improvements")},
            "own_low_star_reviews": [r["body"] for r in own.get("raw_low_star") or []][:8],
        }
        description = description or str(listing.get("title") or "")

    step("The agent is looking at your product" + (" photo" if jpeg else ""))
    spec = analyze_photo(jpeg, complaints or [], description, context or None)
    state["spec"], state["jpeg"] = spec, jpeg

    terms = spec["search_terms"]
    step(f"Searching the market on ZooData: “{terms}”")
    market = market_picture(terms)
    state["market"], state["terms"] = market, terms

    exclude = (state["listing"] or {}).get("asin")
    asins = competitor_asins(market.get("listings") or [], exclude)
    you = _you(state["listing"], state["own_reviews"], price)
    state["you"] = you

    # Real buyer words first: the most helpful reviews of you and the top sellers, counted in code
    # (about 3 seconds). They also supply the competitor themes, so ZooData's slow aggregate
    # endpoint (about 2 minutes) is only a fallback.
    state["evidence"] = state["grounded"] = None
    evidence = None
    comps = evidence_competitors(state["listing"], market)
    if market.get("source") == "live_zoodata" and comps:
        step(f"Reading the most helpful buyer reviews ({len(comps)} listings)")
        try:
            found = build_evidence(comps, gather_reviews([c["asin"] for c in comps]))
            if found["n_low"] >= MIN_LOW_STAR_FOR_EVIDENCE:
                evidence = found
        except Exception as exc:  # the rest of the analysis still stands
            state["evidence_error"] = f"{type(exc).__name__}: {exc}"[:200]

    if evidence:
        comp = themes_from_evidence(evidence)
    else:
        step(f"Reading competitors' reviews ({len(asins)} top sellers)" if asins else "Competitors' reviews: none available")
        comp = review_themes(asins, SAMPLE_COMPETITOR_THEMES) if asins else None
        if comp is None and market.get("source") != "live_zoodata":
            comp = {**SAMPLE_COMPETITOR_THEMES, "note": "Sample themes (no live market data)"}
    state["competitor_reviews"] = comp

    step("The agent is comparing you with competitors" + (" and citing the reviews" if evidence else ""))
    economics = {"price": you.get("price"), "note": "seller costs are applied on the page"}
    with ThreadPoolExecutor(max_workers=2) as pool:  # the two agent calls are independent
        compare_future = pool.submit(compare_with_competitors, you, _competitors(market, comp), market)
        advise_future = pool.submit(advise_evidence, terms, evidence, economics, market) if evidence else None
        state["comparison"] = compare_future.result()
        if advise_future is not None:
            grounded = advise_future.result()
            state["evidence"], state["grounded"] = evidence, grounded
            if grounded["source"] == "zoowork_agent":
                state["spec"]["improvements"] = grounded_to_spec(grounded)
    step("Analysis done")
    return state


def auto_select(improvements: list[dict[str, Any]]) -> list[int]:
    """Autopilot's pick: every change the agent is at least medium-confident in (else the first)."""
    picked = [i for i, imp in enumerate(improvements, 1) if imp.get("confidence") in ("medium", "high")]
    return picked or ([1] if improvements else [])
