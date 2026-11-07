"""Product-to-PO, Step 1: from a product to a better, checked product, backed by real reviews.

Run from the repo root:
    .venv\\Scripts\\streamlit run dashboard/app.py   (this page is in the sidebar)

Start from a description or a pasted Amazon listing. The page reads the market (ZooData), pulls the
most helpful low-star and high-star reviews of the competing listings, counts the themes, and asks
an advisor agent (ZooWork) for product changes. The agent may only cite themes and review ids that
exist; every count shown is computed in code (tools/reviews.py). Margin math is deterministic
(tools/unit_economics.py). Cost and price effects of a change are the agent's estimates.
"""

from __future__ import annotations

import html
import re
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import style  # noqa: E402
from agent.advisor import advise_evidence  # noqa: E402
from tools.market import market_picture  # noqa: E402
from tools.reviews import build_evidence, fetch_listing, gather_reviews, parse_asin  # noqa: E402
from tools.unit_economics import (  # noqa: E402
    EVENTS,
    Inputs,
    apply_changes,
    apply_event,
    breakeven_price_lift,
    compute,
    price_to_hold_margin,
    supplier_cost_to_hold_margin,
)

GREEN, AMBER, RED, GREY, BLUE = style.GREEN, style.AMBER, style.RED, style.GREY, style.BLUE
DEFAULT_KEYWORD = "women linen wide leg pants"

# Where a change shows on the product photo: (x%, y%).
REGION_XY = {"upper": (50, 20), "waist": (50, 38), "lower_body": (50, 62), "hem": (50, 88), "whole": (84, 50)}

st.set_page_config(page_title="Product-to-PO", page_icon=":package:", layout="wide")
style.inject()


def money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def pct(x: float) -> str:
    return f"{x:.1%}"


@st.cache_data(ttl=600, show_spinner="Reading the market on ZooData...")
def get_market(keyword: str) -> dict[str, Any]:
    return market_picture(keyword)


def keyword_from_listing(listing: dict[str, Any]) -> str:
    """A search phrase from a listing title: drop the brand, keep the first few words."""
    title = re.sub(r"[^\w\s'-]", " ", listing["title"])
    brand = (listing.get("brand") or "").lower()
    words = [w for w in title.split() if w.lower() != brand]
    return " ".join(words[:6])


def pick_competitors(market: dict[str, Any], listing: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    skip: set[str] = set()
    if listing:
        out.append({"asin": listing["asin"], "name": f"Your listing ({listing['asin']})", "role": "your listing"})
        skip |= {listing["asin"], listing.get("parent_asin") or ""}
    for item in market.get("listings", []):
        if not item.get("asin") or item["asin"] in skip or item.get("parent_asin") in skip:
            continue
        out.append({"asin": item["asin"], "name": item.get("brand") or (item["title"] or "")[:28], "role": "competitor"})
        if len(out) >= 3:
            break
    return out


# ---------- sidebar: where to start, and the seller's numbers ----------

with st.sidebar:
    st.markdown("### Start from")
    mode = st.radio("Start from", ["Describe it", "Amazon listing"], label_visibility="collapsed")
    listing: dict[str, Any] | None = None
    if mode == "Amazon listing":
        link = st.text_input("Amazon link or ASIN", placeholder="https://www.amazon.com/dp/B0F9KYFRB6")
        if st.button("Load listing", width="stretch"):
            asin = parse_asin(link)
            if not asin:
                st.error("Could not find an ASIN in that.")
            else:
                try:
                    with st.spinner("Reading the listing on ZooData (up to 30 seconds the first time)..."):
                        st.session_state["listing"] = fetch_listing(asin)
                except Exception as exc:
                    st.error(f"Could not load the listing: {exc}")
        listing = st.session_state.get("listing")

    slot = listing["asin"] if listing else "manual"
    default_kw = keyword_from_listing(listing) if listing else DEFAULT_KEYWORD
    keyword = st.text_input("Market search keyword", default_kw, key=f"kw_{slot}")

    st.markdown("### Your numbers (per unit)")
    default_price = float(listing["price"]) if listing and listing.get("price") else 29.99
    price = st.number_input("Selling price ($)", 1.0, 500.0, default_price, 0.5, key=f"price_{slot}")
    supplier_cost = st.number_input("Supplier cost ($)", 0.1, 500.0, 9.00, 0.25)
    shipping = st.number_input("Shipping to Amazon ($)", 0.0, 100.0, 1.50, 0.25)
    duty_rate = st.number_input("Duty rate (%)", 0.0, 200.0, 12.0, 1.0) / 100
    st.caption("Enter the duty rate that applies to your product and origin. It is not looked up yet.")
    referral = st.number_input("Amazon referral fee (%)", 0.0, 50.0, 15.0, 0.5) / 100
    fba_fee = st.number_input("FBA fulfilment fee ($)", 0.0, 100.0, 5.40, 0.1)
    ad_rate = st.number_input("Ad spend (% of price)", 0.0, 50.0, 10.0, 1.0) / 100

base_inputs = Inputs(price, supplier_cost, shipping, duty_rate, referral, fba_fee, ad_rate)
market = get_market(keyword)
today = compute(base_inputs)

# ---------- header ----------

left, right = st.columns([3, 2])
with left:
    st.markdown("## Product-to-PO")
    st.markdown(
        '<span class="subtle">Step 1: from a product to a better, checked product, backed by real customer '
        "reviews. A person approves every money step.</span>",
        unsafe_allow_html=True,
    )
with right:
    if market["source"] == "live_zoodata":
        badge = '<span class="badge badge-live">LIVE ZOODATA</span> <span class="subtle">Amazon listings and reviews</span>'
    else:
        badge = (
            '<span class="badge badge-sample">SAMPLE MARKET DATA</span> '
            f'<span class="subtle">{html.escape(str(market.get("note", "")))}</span>'
        )
    st.markdown(f"<div style='text-align:right'>{badge}</div>", unsafe_allow_html=True)

if listing:
    lc1, lc2 = st.columns([1, 4])
    with lc1:
        if listing.get("image"):
            st.image(listing["image"], width=150)
    with lc2:
        st.markdown(f"**{listing['title']}**")
        meta = [listing.get("brand"), money(listing["price"]) if listing.get("price") else None,
                f"{listing['rating']} stars ({listing['rating_count']:,} ratings)" if listing.get("rating") else None,
                listing["asin"]]
        st.markdown(f"<span class='subtle'>{' &middot; '.join(str(m) for m in meta if m)}</span>", unsafe_allow_html=True)
        for f in listing.get("features", [])[:3]:
            st.markdown(f"<span class='subtle'>&bull; {html.escape(f[:140])}</span>", unsafe_allow_html=True)

# ---------- 1. market picture ----------

st.markdown("### 1. Market picture")
mp = market["price"]
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Typical price", money(mp["median"]))
m1.caption(f"Middle half: {money(mp['p25'])} to {money(mp['p75'])}")
m2.metric("Est. monthly sales", f"{market['monthly_sales_floor']:,}")
m2.caption(f"Top {market['n']} listings, a floor estimate")
m3.metric("Avg rating", f"{market['avg_rating']}" if market["avg_rating"] else "n/a")
m3.caption(f"{market['median_rating_count']:,} ratings (median)" if market["median_rating_count"] else " ")
m4.metric("Sellers per listing", f"{market['median_sellers']}" if market["median_sellers"] else "n/a")
m4.caption("Median across listings")
vs_median = price / mp["median"] - 1
m5.metric("Your price vs median", f"{vs_median:+.0%}")
m5.caption(f"Your price {money(price)}")
if market["listings"]:
    with st.expander("Top listings"):
        st.dataframe(pd.DataFrame(market["listings"]).drop(columns=["image"], errors="ignore"), width="stretch", hide_index=True)

# ---------- 2. unit economics ----------

st.markdown("### 2. What each unit really earns")
c1, c2 = st.columns([3, 2])
parts = dict(today["parts"])
parts["Profit"] = today["profit"]
bar_df = pd.DataFrame({"Part": list(parts), "Dollars": list(parts.values()), "order": range(len(parts))})
palette = [BLUE, "#7f8fa6", AMBER, RED, "#c07af0", "#e4a0a0", GREEN]
bar = (
    alt.Chart(bar_df)
    .mark_bar(size=46)
    .encode(
        x=alt.X("sum(Dollars):Q", title=f"Where the {money(price)} goes", axis=alt.Axis(labels=False, ticks=False)),
        color=alt.Color("Part:N", sort=list(parts), scale=alt.Scale(domain=list(parts), range=palette),
                        legend=alt.Legend(orient="bottom", title=None)),
        order=alt.Order("order:Q"),
        tooltip=["Part", alt.Tooltip("Dollars:Q", format="$.2f")],
    )
    .properties(height=150)
)
c1.altair_chart(bar, width="stretch")
k1, k2, k3 = c2.columns(3)
k1.metric("Landed cost", money(today["landed"]))
k2.metric("Profit / unit", money(today["profit"]))
k3.metric("Margin", pct(today["margin"]))
c2.caption(f"Return on landed cost: {pct(today['roi'])}. Fees, duty and ad rate are your inputs, not looked-up facts.")

# ---------- 3. cost event ----------

st.markdown("### 3. A cost event hits")
event_key = st.radio(
    "Event", list(EVENTS), format_func=lambda k: EVENTS[k][0], horizontal=True, label_visibility="collapsed"
)
after_event_inputs = apply_event(base_inputs, event_key)
after_event = compute(after_event_inputs)

if event_key == "none":
    st.info("Pick an event to see what it does to your margin and what to do about it.")
else:
    e1, e2, e3 = st.columns(3)
    e1.metric("Margin", pct(after_event["margin"]), f"{(after_event['margin'] - today['margin']) * 100:+.1f} pts")
    e2.metric("Profit / unit", money(after_event["profit"]), f"{money(after_event['profit'] - today['profit'])}")
    e3.metric("Landed cost", money(after_event["landed"]), f"{money(after_event['landed'] - today['landed'])}", delta_color="inverse")

    st.markdown(f"**Ways to get back to {pct(today['margin'])}:**")
    a1, a2, a3 = st.columns(3)
    need_price = price_to_hold_margin(after_event_inputs, today["margin"])
    need_cost = supplier_cost_to_hold_margin(after_event_inputs, today["margin"])
    with a1:
        if need_price:
            fits = "within the range competitors charge" if need_price <= mp["p75"] else "above what most competitors charge"
            st.markdown(
                f'<div class="card"><h4>Raise the price to {money(need_price)}</h4>'
                f'<span class="subtle">{need_price / price - 1:+.0%} vs today, {fits} '
                f"(75th percentile {money(mp['p75'])}).</span></div>",
                unsafe_allow_html=True,
            )
    with a2:
        if need_cost:
            st.markdown(
                f'<div class="card"><h4>Cut the supplier cost to {money(need_cost)}</h4>'
                f'<span class="subtle">{need_cost / supplier_cost - 1:+.0%} vs {money(supplier_cost)} today. '
                "Worth a quote round with other suppliers.</span></div>",
                unsafe_allow_html=True,
            )
    with a3:
        lift = (need_price - price) if need_price else 0
        st.markdown(
            f'<div class="card"><h4>Improve the product</h4>'
            f'<span class="subtle">A better product can carry about {money(lift)} more on price. '
            "See what buyers say below.</span></div>",
            unsafe_allow_html=True,
        )

# ---------- 4. what buyers say ----------

st.markdown("### 4. What buyers actually say")
competitors = pick_competitors(market, listing)
if market["source"] != "live_zoodata" or not competitors:
    st.warning("Reviews need live ZooData results. Check the keyword and the ZooData key.")
else:
    names = ", ".join(f"{c['name']}" for c in competitors)
    st.markdown(
        f"<span class='subtle'>Reads the most helpful low-star and high-star reviews of: {html.escape(names)}.</span>",
        unsafe_allow_html=True,
    )

evidence_key = (keyword, tuple(c["asin"] for c in competitors))
if st.button("Read the reviews and ask the advisor", type="primary", disabled=not competitors or market["source"] != "live_zoodata"):
    with st.status("Working...", expanded=True) as status:
        status.update(label="Fetching reviews from ZooData")
        st.write(f"Fetching reviews for {len(competitors)} listings")
        fetched = gather_reviews([c["asin"] for c in competitors])
        evidence = build_evidence(competitors, fetched)
        st.write(f"Read {evidence['n_low']} low-star and {evidence['n_high']} high-star reviews")
        status.update(label="The advisor agent is reading them")
        result = advise_evidence(
            keyword, evidence,
            {"price": price, "landed": round(after_event["landed"], 2), "margin": round(after_event["margin"], 3)},
            market,
        )
        status.update(label="Done", state="complete", expanded=False)
    st.session_state["advice"] = {"key": evidence_key, "evidence": evidence, "result": result}

advice = st.session_state.get("advice")
selected: list[dict[str, Any]] = []
if advice and advice["key"] == evidence_key:
    evidence, result = advice["evidence"], advice["result"]

    for src in evidence["sources"]:
        if src.get("error"):
            st.warning(f"Could not read reviews for {src['name']}: {src['error']}")
    st.markdown(
        f"**Read {evidence['n_low']} low-star (1 to 3) and {evidence['n_high']} high-star reviews** "
        f"across {len(evidence['sources'])} listings, the most helpful first."
    )

    t1, t2 = st.columns([3, 2])
    with t1:
        theme_df = pd.DataFrame(
            [
                {"Theme": t["name"], "Type": t["kind"], "Reviews": t["count"], "Share of low-star": t["share"],
                 "Avg rating": t["avg_rating"]}
                for t in evidence["themes"][:8]
            ]
        )
        st.dataframe(
            theme_df.style.format({"Share of low-star": "{:.0%}", "Avg rating": "{:.1f}"}),
            width="stretch", hide_index=True, height=318,
        )
    with t2:
        st.markdown("**What buyers like (keep this)**")
        likes = ", ".join(p["name"] for p in evidence["positives"][:6]) or "n/a"
        st.markdown(f"<span class='subtle'>{html.escape(likes)}</span>", unsafe_allow_html=True)
        st.caption("Themes are ZooData's AI tags on each review. Counts are reviews mentioning the theme.")

    if result.get("note"):
        st.warning(result["note"])
    is_agent = result["source"] == "zoowork_agent"
    st.markdown(
        f'<span class="badge badge-{"live" if is_agent else "sample"}">{"ZOOWORK ADVISOR AGENT" if is_agent else "RULE-BASED"}</span> '
        f'<span class="subtle">{html.escape(result["summary"])}</span>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Each change cites real reviews; the counts come from the data. Cost and price effects are estimates. "
        "Tick the changes you want to price in."
    )
    for idx, item in enumerate(result["improvements"]):
        sup = item["support"]
        picked = st.checkbox(f"{idx + 1}. {item['title']}", key=f"imp_{idx}_{item['title']}", value=False)
        st.markdown(
            f"<span class='subtle'>{html.escape(item.get('observation', ''))}<br>"
            f"<b>Backed by {sup['count']} of {sup['of']} low-star reviews ({sup['share']:.0%}), avg {sup['avg_rating']} stars.</b> "
            f"+{money(item['unit_cost_delta'])} cost, +{money(item['price_delta'])} price ({item['confidence']} confidence). "
            f"Breaks even at +{money(breakeven_price_lift(after_event_inputs, item['unit_cost_delta']))} price. "
            f"Risk: {html.escape(item['risk'])}</span>",
            unsafe_allow_html=True,
        )
        cited = item["review_ids"] or sup["review_ids"][:3]
        with st.expander(f"Read the reviews behind change {idx + 1}"):
            for rid in cited[:3]:
                r = evidence["reviews"][rid]
                st.markdown(
                    f"> {r['text']}\n>\n> *{r['rating']:.0f} stars, {r['helpful']} found helpful"
                    f"{', verified purchase' if r['verified'] else ''}, on {r['competitor']} ({rid})*"
                )
        if picked:
            selected.append(
                {**item, "n": idx + 1,
                 "fixes": f"{sup['count']} of {sup['of']} low-star reviews: {', '.join(item['themes'][:2]) or item['title']}"}
            )

    # margin with the selected changes, on top of the cost event
    cost_add = sum(s["unit_cost_delta"] for s in selected)
    price_add = sum(s["price_delta"] for s in selected)
    improved_inputs = apply_changes(after_event_inputs, cost_add, price_add)
    improved = compute(improved_inputs)

    st.session_state["rfq_context"] = {
        "keyword": keyword,
        "changes": [
            {k: s[k] for k in ("title", "fixes", "unit_cost_delta", "price_delta", "supplier_ask")} for s in selected
        ],
        "inputs": asdict(after_event_inputs),
        "price_add": price_add,
    }

    st.markdown("**Result with the selected changes**")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("New price", money(improved["price"]), f"{money(price_add)} vs now", delta_color="off")
    r2.metric("Landed cost", money(improved["landed"]), f"{money(improved['landed'] - after_event['landed'])}", delta_color="inverse")
    r3.metric("Profit / unit", money(improved["profit"]), f"{money(improved['profit'] - after_event['profit'])}")
    r4.metric("Margin", pct(improved["margin"]), f"{(improved['margin'] - after_event['margin']) * 100:+.1f} pts")
    st.caption(
        "These changes also aim at fewer returns and better ratings. That benefit is not modelled here, "
        "so the margin shown is the direct cost and price effect only."
    )
    if selected and improved["price"] > mp["p75"]:
        st.warning(
            f"The new price {money(improved['price'])} is above the 75th percentile of competitors "
            f"({money(mp['p75'])}). Buyers may not accept it without strong reviews."
        )

    # before / after view
    st.markdown("**Before and after** (annotated, not a render)")
    img = (listing or {}).get("image") or market.get("top_image")
    b1, b2 = st.columns(2)
    for col, label, pins in ((b1, "Before", []), (b2, "After", selected)):
        with col:
            st.markdown(f'<span class="subtle">{label}</span>', unsafe_allow_html=True)
            if img:
                counts: dict[str, int] = defaultdict(int)
                pin_html = ""
                for s in pins:
                    x, y = REGION_XY[s["region"]]
                    x += 14 * counts[s["region"]]
                    counts[s["region"]] += 1
                    pin_html += f'<div class="pin" style="left:{x}%;top:{y}%">{s["n"]}</div>'
                st.markdown(
                    f'<div style="position:relative;max-width:420px">'
                    f'<img src="{html.escape(img)}" style="width:100%;border-radius:8px"/>{pin_html}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.info("No product image available.")
    if selected:
        for s in selected:
            st.markdown(
                f"- **{s['n']}. {s['title']}**: {s['fixes']}. +{money(s['unit_cost_delta'])} cost, "
                f"+{money(s['price_delta'])} price."
            )
        st.caption(
            "The photo is "
            + ("your listing's main image" if listing else "the top competing listing, used as a stand-in for your product")
            + ". Pin positions are approximate."
        )

    # ---------- 5. draft request for quote ----------
    st.markdown("### 5. Draft request for supplier quotes")
    if selected:
        rfq = (
            f"Product: {keyword}\n"
            f"Target landed-cost ceiling: {money(improved['landed'])} per unit (our estimate)\n"
            "Please quote each change separately, with minimum order, lead time and a sample timeline:\n"
            + "\n".join(f"{s['n']}. {s['supplier_ask']}" for s in selected)
        )
        st.code(rfq, language=None)
        st.info("Next: open **Supplier RFQ** in the sidebar to get quotes from vendors. Nothing is sent from here.")
    else:
        st.caption("Tick one or more changes above to draft the supplier request.")
