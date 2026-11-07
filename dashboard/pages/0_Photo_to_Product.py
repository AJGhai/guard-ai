"""Photo to Product: the photo is the hero.

Drop a photo (or describe the product). A ZooWork agent reads it and writes a spec with weak
points pinned on the photo; its search terms drive a live ZooData market search shown as a strip
of competitor thumbnails; tick improvements and cost and margin update live with a before and
after; download a one-page spec sheet for suppliers; continue to Supplier RFQ.

All margin math is tools/unit_economics.py. The agent only describes the product and proposes
changes with rough cost and price effects, labelled as estimates.
"""

from __future__ import annotations

import hashlib
import html
import urllib.parse
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))
load_dotenv(ROOT / ".env")

import style  # noqa: E402
from agent.photo_spec import analyze_photo, prepare_image  # noqa: E402
from tools.market import market_picture  # noqa: E402
from tools.spec_sheet import build_spec_sheet, data_uri, pinned_photo_html, sample_photo  # noqa: E402
from tools.unit_economics import EVENTS, Inputs, apply_changes, apply_event, compute  # noqa: E402

GREEN, AMBER, RED, GREY = style.GREEN, style.AMBER, style.RED, style.GREY
LETTERS = "ABCDE"
SAMPLE_COMPLAINTS = """Runs very small, had to order two sizes up
Fabric is see-through in white
Elastic waistband loosened after a few washes"""
SAMPLE_DESCRIPTION = "Lightweight linen wide-leg pants for women, high elastic waist, beige, full length"

st.set_page_config(page_title="Photo to Product", page_icon=":camera:", layout="wide")
style.inject()
st.markdown(
    """<style>
    .strip { display:flex; gap:12px; overflow-x:auto; padding:4px 2px 10px; }
    .thumb { flex:0 0 150px; background:#111823; border:1px solid #1e2a3a; border-radius:8px; padding:8px; }
    .thumb img { width:100%; height:150px; object-fit:contain; background:#fff; border-radius:6px; }
    .thumb .t { font-size:0.72rem; color:#c9d1d9; margin-top:6px; height:2.6em; overflow:hidden; }
    .thumb .p { font-weight:700; margin-top:4px; }
    .thumb .s { font-size:0.68rem; color:#6b7785; }
    .wp { margin:4px 0; font-size:0.9rem; }
    .letter { display:inline-block; width:20px; height:20px; border-radius:50%; background:#ff5c6c;
      color:#04130d; text-align:center; font-weight:700; font-size:0.75rem; line-height:20px; margin-right:6px; }
    </style>""",
    unsafe_allow_html=True,
)


def money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


@st.cache_data(ttl=600, show_spinner="Searching the market on ZooData...")
def get_market(keyword: str) -> dict[str, Any]:
    return market_picture(keyword)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_image(url: str) -> bytes | None:
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        return prepare_image(resp.content)
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def sample_jpeg() -> bytes:
    return sample_photo()


# ---------- sidebar: the seller's numbers ----------

with st.sidebar:
    st.markdown("### Your numbers (per unit)")
    price = st.number_input("Selling price ($)", 1.0, 500.0, 29.99, 0.5)
    supplier_cost = st.number_input("Supplier cost ($)", 0.1, 500.0, 9.00, 0.25)
    shipping = st.number_input("Shipping to Amazon ($)", 0.0, 100.0, 1.50, 0.25)
    duty_rate = st.number_input("Duty rate (%)", 0.0, 200.0, 12.0, 1.0) / 100
    referral = st.number_input("Amazon referral fee (%)", 0.0, 50.0, 15.0, 0.5) / 100
    fba_fee = st.number_input("FBA fulfilment fee ($)", 0.0, 100.0, 5.40, 0.1)
    ad_rate = st.number_input("Ad spend (% of price)", 0.0, 50.0, 10.0, 1.0) / 100
    event_key = st.selectbox(
        "Cost event", list(EVENTS), format_func=lambda k: EVENTS[k][0],
        help="Applied before the improvements, e.g. a tariff rise on the product's origin.",
    )
    st.caption("Your inputs, not facts about Amazon or customs.")

inputs = Inputs(price, supplier_cost, shipping, duty_rate, referral, fba_fee, ad_rate)
after_event = apply_event(inputs, event_key)

# ---------- header ----------

st.markdown("## Photo to Product")
st.markdown(
    '<span class="subtle">Drop a photo or describe the product. The ZooWork agent reads it, finds '
    "weak points, checks the market on ZooData and proposes improvements you can price in.</span>",
    unsafe_allow_html=True,
)

# ---------- 1. the product ----------

st.markdown("### 1. Your product")
mode = st.radio("Start from", ["Drop a photo", "Describe it"], horizontal=True, label_visibility="collapsed")
photo_bytes: bytes | None = None
description = ""
if mode == "Drop a photo":
    upload = st.file_uploader("Product photo", type=["png", "jpg", "jpeg", "webp"])
    if upload is not None:
        # An uploaded photo always wins over the sample.
        try:
            photo_bytes = prepare_image(upload.getvalue())
            st.image(photo_bytes, width=180, caption="Your photo")
        except Exception:
            st.error("Could not read that image. Try a PNG or JPEG.")
    else:
        st.caption("No photo uploaded yet, so the sample sketch will be used.")
        photo_bytes = sample_jpeg()
else:
    description = st.text_area("Describe the product", SAMPLE_DESCRIPTION, height=80)

with st.expander("Buyer complaints from competing listings (optional)"):
    complaints_text = st.text_area(
        "One per line", "", height=100, label_visibility="collapsed",
        placeholder="Paste complaints about THIS kind of product, one per line, e.g.\n" + SAMPLE_COMPLAINTS,
    )
    st.caption("Leave empty to let the agent judge from the photo alone.")
complaints = [c.strip() for c in complaints_text.splitlines() if c.strip()]

ready = bool(photo_bytes) if mode == "Drop a photo" else bool(description.strip())
basis = hashlib.sha256(photo_bytes or description.encode()).hexdigest()[:16]
run_key = (mode, basis, tuple(complaints))
if st.button("Analyze with the agent", type="primary", disabled=not ready):
    with st.spinner("The agent is looking at your product..."):
        result = analyze_photo(photo_bytes, complaints, description)
    st.session_state["photo_spec"] = {"key": run_key, "result": result, "jpeg": photo_bytes}

saved = st.session_state.get("photo_spec")
if not saved:
    st.info("Pick a photo (or the sample) and press **Analyze with the agent**. It takes about 10 to 20 seconds.")
    st.stop()
if saved["key"] != run_key:
    st.caption("Inputs changed since the last analysis. Press **Analyze with the agent** to refresh.")

spec: dict[str, Any] = saved["result"]
if spec.get("note"):
    st.warning(spec["note"])
source_badge = (
    '<span class="badge badge-live">ZOOWORK AGENT</span>' if spec.get("source") == "zoowork_agent"
    else '<span class="badge badge-sample">SAMPLE SPEC</span>'
)

# ---------- 2. market (search terms come from the agent) ----------

st.markdown("### 2. The market")
terms = st.text_input("Search terms (written by the agent; edit if needed)", spec["search_terms"])
market = get_market(terms)
live = market.get("source") == "live_zoodata"
listings = sorted(market.get("listings") or [], key=lambda r: -(r.get("monthly_sales_floor") or 0))

# The photo shown everywhere: the seller's own, else the top listing's (stand-in), else the sketch.
jpeg: bytes | None = saved["jpeg"]
stand_in = None
if jpeg is None:
    top_img = next((r["image"] for r in listings if r.get("image")), None) or market.get("top_image")
    jpeg = fetch_image(top_img) if top_img else None
    stand_in = "top competing listing (ZooData)" if jpeg else None
if jpeg is None:
    jpeg = sample_jpeg()
    stand_in = stand_in or "sample illustration"
src = data_uri(jpeg)

p = market["price"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Median price", money(p["median"]), f"{money(p['p25'])} to {money(p['p75'])} middle half", delta_color="off")
m2.metric("Est. monthly units", f"{market['monthly_sales_floor']:,}", f"{market['n']} listings", delta_color="off")
m3.metric("Avg rating", f"{market['avg_rating']}" if market.get("avg_rating") else "–")
m4.metric("Typical sellers per listing", f"{market['median_sellers']}" if market.get("median_sellers") else "–")
st.markdown(
    f'<span class="badge {"badge-live" if live else "badge-sample"}">'
    f'{"LIVE ZOODATA" if live else "SAMPLE MARKET DATA"}</span> '
    f'<span class="subtle">{html.escape(market.get("note") or ("Amazon listings for " + terms))}</span>',
    unsafe_allow_html=True,
)
with_images = [r for r in listings if r.get("image")][:12]
if with_images:
    cards = "".join(
        f'<div class="thumb"><img src="{html.escape(r["image"])}" loading="lazy"/>'
        f'<div class="t">{html.escape((r.get("title") or "")[:80])}</div>'
        f'<div class="p">{money(r["price"])}</div>'
        f'<div class="s">{"★ " + str(r["rating"]) if r.get("rating") else ""}'
        f'{" · #" + format(r["bsr"], ",") + " rank" if r.get("bsr") else ""}</div>'
        f'<div class="s">{format(r["monthly_sales_floor"], ",") + "+ / mo" if r.get("monthly_sales_floor") else ""}</div></div>'
        for r in with_images
    )
    st.markdown(f'<div class="strip">{cards}</div>', unsafe_allow_html=True)
else:
    st.caption("Competitor thumbnails appear with live ZooData (add ZOODATA_API_KEY to .env).")

# ---------- 3. what the agent sees ----------

st.markdown("### 3. What the agent sees")
left, right = st.columns([2, 3], gap="large")
weak = spec.get("weak_points") or []
with left:
    st.markdown(pinned_photo_html(src, weak, RED, list(LETTERS[: len(weak)])), unsafe_allow_html=True)
    if stand_in:
        st.caption(f"No photo given: showing the {stand_in} as a stand-in. Pins are approximate.")
    else:
        st.caption("Pins mark approximate areas, not exact points.")
with right:
    st.markdown(f"{source_badge} &nbsp;**{html.escape(spec['title'])}**", unsafe_allow_html=True)
    st.markdown(f"<span class='subtle'>{html.escape(spec['product_type'])}</span>", unsafe_allow_html=True)
    if spec.get("materials"):
        st.markdown("**Materials:** " + ", ".join(spec["materials"]))
    if spec.get("features"):
        st.markdown("**Features:** " + " · ".join(spec["features"]))
    st.markdown("**Weak points**")
    for letter, w in zip(LETTERS, weak):
        st.markdown(
            f'<div class="wp"><span class="letter">{letter}</span><b>{html.escape(w["title"])}</b> '
            f'<span class="subtle">{html.escape(w["why"])}</span></div>',
            unsafe_allow_html=True,
        )
    if spec.get("summary"):
        st.markdown(f"<span class='subtle'>{html.escape(spec['summary'])}</span>", unsafe_allow_html=True)

# ---------- 4. improve it: tick changes, numbers update live ----------

st.markdown("### 4. Improve it")
st.caption("Cost and price effects are the agent's estimates. Tick the changes to price in.")
selected: list[dict[str, Any]] = []
for i, imp in enumerate(spec["improvements"], 1):
    c1, c2 = st.columns([2, 5])
    with c1:
        on = st.checkbox(f"{i}. {imp['title']}", value=i <= 2, key=f"imp_{basis}_{i}")
    with c2:
        st.markdown(
            f"<span class='subtle'>Fixes: {html.escape(imp['fixes'])} · +{money(imp['unit_cost_delta'])} cost, "
            f"+{money(imp['price_delta'])} price ({imp['confidence']} confidence). "
            f"Risk: {html.escape(imp['risk'])}</span>",
            unsafe_allow_html=True,
        )
    if on:
        selected.append({**imp, "n": i})

base = compute(after_event)
cost_add = sum(s["unit_cost_delta"] for s in selected)
price_add = sum(s["price_delta"] for s in selected)
improved = compute(apply_changes(after_event, cost_add, price_add))
r1, r2, r3, r4 = st.columns(4)
r1.metric("Price", money(improved["price"]), f"{money(price_add)} vs now")
r2.metric("Landed cost", money(improved["landed"]), money(improved["landed"] - base["landed"]), delta_color="inverse")
r3.metric("Profit / unit", money(improved["profit"]), money(improved["profit"] - base["profit"]))
r4.metric("Margin", f"{improved['margin']:.1%}", f"{(improved['margin'] - base['margin']) * 100:+.1f} pts")
if event_key != "none":
    st.caption(f"Includes the cost event: {EVENTS[event_key][0]}.")
if selected and improved["price"] > p["p75"]:
    st.warning(
        f"The new price {money(improved['price'])} is above what the top quarter of competitors charge "
        f"({money(p['p75'])}). Buyers may not accept it without strong reviews."
    )

b1, b2 = st.columns(2, gap="large")
with b1:
    st.markdown('<span class="subtle">Before: weak points</span>', unsafe_allow_html=True)
    st.markdown(pinned_photo_html(src, weak, RED, list(LETTERS[: len(weak)])), unsafe_allow_html=True)
with b2:
    st.markdown('<span class="subtle">After: selected changes</span>', unsafe_allow_html=True)
    st.markdown(pinned_photo_html(src, selected, GREEN, [str(s["n"]) for s in selected]), unsafe_allow_html=True)
for s in selected:
    st.markdown(f"- **{s['n']}. {s['title']}**: {s['fixes']}. +{money(s['unit_cost_delta'])} cost, +{money(s['price_delta'])} price.")

# ---------- 5. where to source it ----------

st.markdown("### 5. Where to source it")
sourcing = spec.get("sourcing") or []
if sourcing:
    cols = st.columns(len(sourcing))
    for col, src_row in zip(cols, sourcing):
        q = src_row.get("search_terms") or spec["search_terms"]
        url = "https://www.alibaba.com/trade/search?" + urllib.parse.urlencode({"SearchText": q})
        with col:
            st.markdown(
                f'<div class="card"><h4>{html.escape(src_row["region"])}</h4>'
                f'<div>{html.escape(src_row["why"])}</div>'
                f'<div class="subtle" style="margin-top:6px">Watch out: {html.escape(src_row["watch_out"])}</div>'
                f'<div style="margin-top:8px"><a href="{html.escape(url)}" target="_blank">'
                f'Search “{html.escape(q)}” on Alibaba ↗</a></div></div>',
                unsafe_allow_html=True,
            )
    if spec.get("certifications"):
        st.markdown("**Ask suppliers for:** " + " · ".join(spec["certifications"]))
    st.caption(
        "Regions, not specific factories: we have no supplier database, so verify any supplier yourself. "
        "Check the current US duty for each country, since it changes the landed cost. "
        "Supplier RFQ then simulates quotes from three vendors."
    )
else:
    st.caption("The agent gave no sourcing suggestions this time.")

# ---------- 6. spec sheet + hand-off to Supplier RFQ ----------

st.markdown("### 6. Spec sheet for suppliers")
if not selected:
    st.caption("Tick at least one change to build the spec sheet.")
    st.stop()

# Same hand-off contract as the Product to PO page, so Supplier RFQ picks these changes up.
st.session_state["rfq_context"] = {
    "keyword": spec["title"],
    "changes": [
        {k: s[k] for k in ("title", "fixes", "unit_cost_delta", "price_delta", "supplier_ask")}
        for s in selected
    ],
    "inputs": asdict(after_event),
    "price_add": price_add,
}

sheet = build_spec_sheet(jpeg, spec, selected, improved)
c1, c2 = st.columns([1, 2])
with c1:
    st.download_button(
        "Download spec sheet", sheet.encode(), file_name=f"spec-sheet-{basis}.html",
        mime="text/html", type="primary", width="stretch",
    )
    st.caption("Open it and print to PDF. Nothing is sent.")
with c2:
    try:
        st.page_link("pages/2_Supplier_RFQ.py", label="Next: get quotes from suppliers →", icon="📦")
    except Exception:  # only when this page runs on its own, outside the dashboard
        st.info("Next: open **Supplier RFQ** in the sidebar. Your selected changes carry over.")
with st.expander("Preview the spec sheet", expanded=True):
    components.html(sheet, height=620, scrolling=True)
