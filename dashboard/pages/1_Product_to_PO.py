"""Product-to-PO, Step 1: from a product idea to a better, checked product.

Run from the repo root:
    .venv\\Scripts\\streamlit run dashboard/app.py (this page is in the sidebar)

Flow: market picture (ZooData) -> unit economics -> cost event -> advisor agent (ZooWork)
proposes product changes from buyer complaints -> before/after view -> draft supplier request.
Margin math is deterministic (tools/unit_economics.py); the agent only proposes changes and
rough cost/price effects, which are labelled as estimates.
"""

from __future__ import annotations

import html
import sys
from collections import defaultdict
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
from agent.advisor import advise  # noqa: E402
from tools.market import market_picture  # noqa: E402
from tools.unit_economics import (  # noqa: E402
    EVENTS,
    Inputs,
    apply_changes,
    apply_event,
    compute,
    price_to_hold_margin,
    supplier_cost_to_hold_margin,
)

GREEN, AMBER, RED, GREY, BLUE = style.GREEN, style.AMBER, style.RED, style.GREY, style.BLUE

SAMPLE_COMPLAINTS = """Runs very small, had to order two sizes up
Fabric is see-through in white
Wrinkles badly after one wash
Elastic waistband loosened after a few washes
Color looks different from the photo"""

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


# ---------- sidebar: the seller's numbers ----------

with st.sidebar:
    st.markdown("### Product")
    keyword = st.text_input("What are you selling?", "women linen wide leg pants")

    st.markdown("### Your numbers (per unit)")
    price = st.number_input("Selling price ($)", 1.0, 500.0, 29.99, 0.5)
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
        '<span class="subtle">Step 1: from a product idea to a better, checked product. '
        "A person approves every money step.</span>",
        unsafe_allow_html=True,
    )
with right:
    if market["source"] == "live_zoodata":
        badge = '<span class="badge badge-live">LIVE ZOODATA</span> <span class="subtle">Amazon listings</span>'
    else:
        badge = (
            '<span class="badge badge-sample">SAMPLE MARKET DATA</span> '
            f'<span class="subtle">{html.escape(str(market.get("note", "")))}</span>'
        )
    st.markdown(f"<div style='text-align:right'>{badge}</div>", unsafe_allow_html=True)

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
        st.dataframe(pd.DataFrame(market["listings"]), width="stretch", hide_index=True)

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
    "Event",
    list(EVENTS),
    format_func=lambda k: EVENTS[k][0],
    horizontal=True,
    label_visibility="collapsed",
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
            "See the advisor below.</span></div>",
            unsafe_allow_html=True,
        )

# ---------- 4. improvements ----------

st.markdown("### 4. What buyers want changed")
complaints_text = st.text_area(
    "Buyer complaints from competing listings (sample reviews; paste your own)",
    SAMPLE_COMPLAINTS,
    height=130,
)
complaints = [line.strip() for line in complaints_text.splitlines() if line.strip()]
advice_key = (keyword, tuple(complaints))

if st.button("Ask the advisor agent", type="primary", disabled=not complaints):
    with st.spinner("The advisor agent is working on ZooWork (about 10 seconds)..."):
        economics = {
            "price": price,
            "landed": round(after_event["landed"], 2),
            "margin": round(after_event["margin"], 3),
            "market_median_price": mp["median"],
        }
        st.session_state["advice"] = {"key": advice_key, "result": advise(keyword, complaints, economics)}

advice = st.session_state.get("advice")
selected: list[dict[str, Any]] = []
if advice and advice["key"] == advice_key:
    result = advice["result"]
    if result.get("note"):
        st.warning(result["note"])
    badge = "ZOOWORK AGENT" if result["source"] == "zoowork_agent" else "SAMPLE ANSWER"
    st.markdown(
        f'<span class="badge badge-{"live" if result["source"] == "zoowork_agent" else "sample"}">{badge}</span> '
        f'<span class="subtle">{html.escape(result["summary"])}</span>',
        unsafe_allow_html=True,
    )
    st.caption("Cost and price effects are the agent's estimates. Tick the changes you want to price in.")
    for idx, item in enumerate(result["improvements"]):
        ck, info = st.columns([2, 5])
        picked = ck.checkbox(
            f"{idx + 1}. {item['title']}", key=f"imp_{idx}_{item['title']}", value=False
        )
        info.markdown(
            f'<span class="subtle">Fixes: {html.escape(item["fixes"])} &middot; '
            f"+{money(item['unit_cost_delta'])} cost, +{money(item['price_delta'])} price "
            f"({item['confidence']} confidence). Risk: {html.escape(item['risk'])}</span>",
            unsafe_allow_html=True,
        )
        if picked:
            selected.append({**item, "n": idx + 1})

    # margin with the selected changes, on top of the cost event
    cost_add = sum(s["unit_cost_delta"] for s in selected)
    price_add = sum(s["price_delta"] for s in selected)
    improved_inputs = apply_changes(after_event_inputs, cost_add, price_add)
    improved = compute(improved_inputs)

    st.markdown("**Result with the selected changes**")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("New price", money(improved["price"]), f"{money(price_add)} vs now", delta_color="off")
    r2.metric("Landed cost", money(improved["landed"]), f"{money(improved['landed'] - after_event['landed'])}", delta_color="inverse")
    r3.metric("Profit / unit", money(improved["profit"]), f"{money(improved['profit'] - after_event['profit'])}")
    r4.metric("Margin", pct(improved["margin"]), f"{(improved['margin'] - after_event['margin']) * 100:+.1f} pts")
    if selected and improved["price"] > mp["p75"]:
        st.warning(
            f"The new price {money(improved['price'])} is above the 75th percentile of competitors "
            f"({money(mp['p75'])}). Buyers may not accept it without strong reviews."
        )

    # before / after view
    st.markdown("**Before and after** (annotated, not a render)")
    img = market.get("top_image")
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
            "The photo is the top competing listing, used as a stand-in for your product. "
            "Pin positions are approximate."
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
        st.info("Step 2 (supplier chat and purchase order) comes next. Nothing is sent from here.")
    else:
        st.caption("Tick one or more changes above to draft the supplier request.")
