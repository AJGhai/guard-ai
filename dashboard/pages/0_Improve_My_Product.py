"""Improve My Product: for a seller who already sells on Amazon.

Paste your Amazon listing link (or drop a photo, or describe the product). The ZooWork agent reads
your reviews and your photo, ZooData shows the competitors selling the same product and what
their buyers say, and the agent tells you where you are doing well and where there is room to
improve. Tick improvements and the margin updates live; download a spec sheet for suppliers.

Autopilot runs every step, picks the improvements, gets supplier quotes (Supplier RFQ) and drafts
a purchase order, then stops for your approval. All margin math is tools/unit_economics.py.
"""

from __future__ import annotations

import hashlib
import html
import os
import sys
import urllib.parse
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))
load_dotenv(ROOT / ".env")

import style  # noqa: E402
from agent.photo_spec import prepare_image  # noqa: E402
from agent.sourcing import run_sourcing  # noqa: E402
from tools.annotate import annotate  # noqa: E402
from tools.improve import auto_select, run_pipeline  # noqa: E402
from tools.demo_store import DEMO_FILE, EXAMPLES, load_run, run_path, save_run  # noqa: E402
from tools.listing import asin_from_text  # noqa: E402
from tools.unit_economics import breakeven_price_lift  # noqa: E402
from tools.rfq import VENDORS, build_po, evaluate, po_text, reference_cost, write_po  # noqa: E402
from tools.spec_sheet import build_spec_sheet, data_uri, pinned_photo_html, sample_photo  # noqa: E402
from tools.unit_economics import EVENTS, Inputs, apply_changes, apply_event, compute  # noqa: E402

GREEN, AMBER, RED, GREY = style.GREEN, style.AMBER, style.RED, style.GREY
LETTERS = "ABCDE"
SAMPLE_DESCRIPTION = "Lightweight linen wide-leg pants for women, high elastic waist, beige, full length"
RFQ_QTY, RFQ_DEADLINE = 500, 60   # the Supplier RFQ page's defaults, so it shows the same quotes

st.set_page_config(page_title="Improve My Product", page_icon=":mag:", layout="wide")
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
    .chip { display:inline-block; margin:2px 4px 2px 0; padding:2px 9px; border-radius:999px; font-size:0.78rem; }
    .chip-bad { background:rgba(255,92,108,.12); border:1px solid #ff5c6c; color:#ffb3bb; }
    .chip-good { background:rgba(0,211,149,.12); border:1px solid #00d395; color:#9ff0d4; }
    .cmp { margin:6px 0; } .cmp b { display:block; } .cmp .subtle { display:block; }
    </style>""",
    unsafe_allow_html=True,
)


def money(x: float | None) -> str:
    if x is None:
        return "–"
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def chips(themes: list[dict[str, Any]] | None, kind: str) -> str:
    if not themes:
        return "<span class='subtle'>None found</span>"
    out = []
    for t in themes:
        rate = f" · {t['rate']:.0%}" if t.get("rate") else ""
        out.append(f"<span class='chip chip-{kind}'>{html.escape(t['element'])}{rate}</span>")
    return "".join(out)


def badge(live: bool, live_text: str, sample_text: str) -> str:
    return f'<span class="badge {"badge-live" if live else "badge-sample"}">{live_text if live else sample_text}</span>'


@st.cache_data(show_spinner=False)
def sample_jpeg() -> bytes:
    return sample_photo()


# ---------- sidebar: the seller's numbers ----------

# A price read from the listing is applied here, before the price widget exists (Streamlit rule).
if "pending_price" in st.session_state:
    st.session_state["sb_price"] = st.session_state.pop("pending_price")

with st.sidebar:
    st.markdown("### Your numbers (per unit)")
    price = st.number_input("Selling price ($)", 1.0, 500.0, 29.99, 0.5, key="sb_price")
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
    st.caption("Your inputs, not facts about Amazon or customs. Price is filled in from your listing.")

inputs = Inputs(price, supplier_cost, shipping, duty_rate, referral, fba_fee, ad_rate)
after_event = apply_event(inputs, event_key)

# ---------- header ----------

st.markdown("## Improve My Product")
st.markdown(
    '<span class="subtle">For sellers already on Amazon. The ZooWork agent reads your reviews and your '
    "photo, ZooData shows the competitors selling the same product, and you see where you win and "
    "where to improve.</span>",
    unsafe_allow_html=True,
)

# ---------- 1. your product ----------

st.markdown("### 1. Your product")


def _pick_example() -> None:
    """Fill the link box from the example picker (runs before the page reruns)."""
    label = st.session_state.get("example_pick")
    for e in EXAMPLES:
        if e["label"] == label:
            st.session_state["link_input"] = f"https://www.amazon.com/dp/{e['asin']}"


mode_label = st.radio(
    "Start from", ["Amazon listing link", "Drop a photo", "Describe it"], horizontal=True,
    label_visibility="collapsed",
)
mode = {"Amazon listing link": "link", "Drop a photo": "photo", "Describe it": "description"}[mode_label]
link, photo_bytes, description = "", None, ""
if mode == "link":
    st.selectbox("Try an example listing", ["Pick an example..."] + [e["label"] for e in EXAMPLES],
                 key="example_pick", on_change=_pick_example)
    link = st.text_input("Your Amazon listing link (or ASIN)", key="link_input",
                         placeholder="https://www.amazon.com/dp/B0XXXXXXXX")
    st.caption("We read the listing, its main photo and its reviews from ZooData (about 4 to 6 credits).")
elif mode == "photo":
    upload = st.file_uploader("Product photo", type=["png", "jpg", "jpeg", "webp"])
    if upload is not None:
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

with st.expander("Extra buyer complaints (optional)"):
    complaints_text = st.text_area(
        "One per line", "", height=90, label_visibility="collapsed",
        placeholder="Paste complaints about THIS product, one per line",
    )
complaints = [c.strip() for c in complaints_text.splitlines() if c.strip()]

ready = {"link": bool(link.strip()), "photo": bool(photo_bytes), "description": bool(description.strip())}[mode]
basis = hashlib.sha256(photo_bytes or (link + description).encode()).hexdigest()[:16]
run_key = (mode, basis, tuple(complaints))

b1, b2, b3 = st.columns([1, 1.3, 1.2])
analyze_clicked = b1.button("Analyze", type="primary", disabled=not ready, width="stretch")
autopilot_clicked = b2.button("🚀 Autopilot to purchase order", disabled=not ready, width="stretch")
link_asin = asin_from_text(link) if mode == "link" else None
instant_file = run_path(link_asin) if link_asin and run_path(link_asin).exists() else DEMO_FILE
instant_clicked = b3.button("⚡ Instant demo", disabled=not instant_file.exists(), width="stretch",
                            help="Replays a recorded real run in under a second: the one for the listing in the box "
                                 "if there is one, else the latest saved. Save one with the button below a result.")
st.caption("Autopilot runs every step, picks the improvements, gets supplier quotes and drafts the order. "
           "It stops only for your approval. About a minute. **Instant demo** replays a recorded run.")

if instant_clicked:
    saved = load_run(instant_file)
    recorded = {**saved["state"], "key": run_key, "recorded": True}
    st.session_state["improve"] = recorded
    for k in ("rfq_context", "rfq_result"):
        if saved.get(k) is not None:
            st.session_state[k] = saved[k]
    st.session_state.pop("po_saved", None)
    recorded_price = (recorded.get("listing") or {}).get("price")
    if recorded_price and abs(recorded_price - price) > 0.005:
        st.session_state["pending_price"] = float(recorded_price)
        st.rerun()


def run_analysis(status: Any) -> dict[str, Any] | None:
    def on_step(label: str) -> None:
        status.update(label=label)
        st.write("⏳ " + label)

    try:
        return run_pipeline(mode, link=link, photo=photo_bytes, description=description,
                            complaints=complaints, price=price, on_step=on_step)
    except ValueError as exc:
        status.update(label=str(exc), state="error")
        return None


if analyze_clicked or autopilot_clicked:
    with st.status("Starting...", expanded=True) as status:
        state = run_analysis(status)
        if state is not None:
            state["key"] = run_key
            state["autopilot"] = None
            if autopilot_clicked:
                spec = state["spec"]
                picked = auto_select(spec["improvements"])
                chosen = [{**spec["improvements"][i - 1], "n": i} for i in picked]
                st.write(f"⏳ Picking improvements: {', '.join(c['title'] for c in chosen)}")
                listing_price = (state.get("listing") or {}).get("price")
                base = apply_event(replace(inputs, price=listing_price or price), event_key)
                changes = [{k: c[k] for k in ("title", "fixes", "unit_cost_delta", "price_delta", "supplier_ask")}
                           for c in chosen]
                price_add = sum(c["price_delta"] for c in chosen)
                product = spec["title"]
                use_agents = bool(os.environ.get("ZOOWORK_API_KEY", "").strip())
                status.update(label="Requesting quotes from three suppliers")
                st.write("⏳ Requesting quotes from three suppliers (simulated vendors)")
                ref = reference_cost(base.supplier_cost, changes)
                rfq = run_sourcing(product, changes, ref, RFQ_QTY, RFQ_DEADLINE, use_agents,
                                   lambda s: st.write("   · " + s))
                rows = evaluate(rfq["quotes"], base, price_add, RFQ_QTY, RFQ_DEADLINE,
                                {v.key: v.duty_rate for v in VENDORS})
                best = next((r for r in rows if r["eligible"]), rows[0] if rows else None)
                st.write("⏳ Ranking quotes on landed cost and drafting the purchase order")
                state["autopilot"] = {
                    "picked": picked, "changes": changes, "price_add": price_add, "base": asdict(base),
                    "rfq": rfq, "rows": rows,
                    "po": build_po(best, product, changes, RFQ_QTY) if best else None,
                    "approved": None, "path": None,
                }
                # Hand the same run to the Supplier RFQ page so it shows these chats and quotes.
                st.session_state["rfq_context"] = {"keyword": product, "changes": changes,
                                                   "inputs": asdict(base), "price_add": price_add}
                st.session_state["rfq_result"] = {
                    "key": (product, tuple(c["title"] for c in changes), RFQ_QTY, RFQ_DEADLINE, use_agents),
                    "result": rfq,
                }
                st.session_state.pop("po_saved", None)
            status.update(label="Done. " + ("Purchase order waiting for your approval below."
                                             if autopilot_clicked else "Scroll down."), state="complete")
            st.session_state["improve"] = state
            listing_price = (state.get("listing") or {}).get("price")
            if listing_price and abs(listing_price - price) > 0.005:
                st.session_state["pending_price"] = float(listing_price)
                st.rerun()

state = st.session_state.get("improve")
if not state:
    st.info("Paste your listing link (or pick a photo) and press **Analyze**, or let **Autopilot** run every step.")
    st.stop()
if state.get("recorded"):
    st.markdown('<span class="badge badge-sample">RECORDED RUN</span> '
                '<span class="subtle">A saved real run, replayed instantly.</span>', unsafe_allow_html=True)
elif state["key"] != run_key:
    st.caption("Inputs changed since the last run. Press **Analyze** to refresh.")
if not state.get("recorded") and st.button("💾 Save this run as the instant demo", key="record_demo"):
    ctx, res = st.session_state.get("rfq_context"), st.session_state.get("rfq_result")
    save_run(state, ctx, res)  # the latest run
    saved_asin = (state.get("listing") or {}).get("asin")
    if saved_asin:
        save_run(state, ctx, res, run_path(saved_asin))  # and this listing's own recording
    st.success("Saved on this machine. Next time, press Instant demo.")

spec: dict[str, Any] = state["spec"]
market: dict[str, Any] = state["market"]
listing: dict[str, Any] | None = state.get("listing")
own: dict[str, Any] | None = state.get("own_reviews")
comp_rev: dict[str, Any] | None = state.get("competitor_reviews")
cmp_: dict[str, Any] = state["comparison"]
auto: dict[str, Any] | None = state.get("autopilot")
for note in [spec.get("note"), (listing or {}).get("note"), (own or {}).get("note"), cmp_.get("note")]:
    if note:
        st.warning(note)

listings = sorted(market.get("listings") or [], key=lambda r: -(r.get("monthly_sales_floor") or 0))
live_market = market.get("source") == "live_zoodata"
jpeg: bytes | None = state.get("jpeg")
stand_in = None
if jpeg is None:
    from tools.improve import fetch_image
    top_img = next((r["image"] for r in listings if r.get("image")), None) or market.get("top_image")
    jpeg = fetch_image(top_img)
    stand_in = "top competing listing (ZooData)" if jpeg else None
if jpeg is None:
    jpeg, stand_in = sample_jpeg(), stand_in or "sample illustration"
src = data_uri(jpeg)

# ---------- 2. your listing and what your buyers say ----------

st.markdown("### 2. Your listing and what your buyers say")
if listing:
    c1, c2 = st.columns([1, 3], gap="large")
    with c1:
        st.image(jpeg, width=180)
    with c2:
        st.markdown(f"{badge(listing.get('source') == 'live_zoodata', 'LIVE ZOODATA', 'SAMPLE LISTING')} "
                    f"**{html.escape(listing.get('title') or '')}**", unsafe_allow_html=True)
        st.markdown(
            f"{money(listing.get('price'))} · ★ {listing.get('rating') or '–'} "
            f"({format(listing.get('rating_count') or 0, ',')} ratings)"
            + (f" · best seller rank #{listing['bsr']:,}" if listing.get("bsr") else "")
        )
        if listing.get("features"):
            st.markdown("<span class='subtle'>" + " · ".join(html.escape(f[:70]) for f in listing["features"][:4])
                        + "</span>", unsafe_allow_html=True)
if own:
    st.markdown(f"**Your buyers complain about** {chips(own.get('issues'), 'bad')}", unsafe_allow_html=True)
    st.markdown(f"**Your buyers like** {chips(own.get('positives'), 'good')}", unsafe_allow_html=True)
    if own.get("raw_low_star"):
        with st.expander(f"Latest low-star reviews ({len(own['raw_low_star'])})"):
            for r in own["raw_low_star"]:
                st.markdown(f"★{r.get('rating')} **{html.escape(r['title'])}**: {html.escape(r['body'][:240])}")
    st.caption(f"From {format(own.get('review_count') or 0, ',')} reviews"
               + (" (sample)" if own.get("source") == "sample" else " via ZooData") + ". % = share of reviews.")
elif not listing:
    st.caption("Paste your listing link to read your own reviews. With a photo or description, the agent works "
               "from what it sees.")

# ---------- 3. the market ----------

st.markdown("### 3. The market")
p = market["price"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Median price", money(p["median"]), f"{money(p['p25'])} to {money(p['p75'])} middle half", delta_color="off")
m2.metric("Est. monthly units", f"{market['monthly_sales_floor']:,}", f"{market['n']} listings", delta_color="off")
m3.metric("Avg rating", f"{market['avg_rating']}" if market.get("avg_rating") else "–")
m4.metric("Typical sellers per listing", f"{market['median_sellers']}" if market.get("median_sellers") else "–")
st.markdown(f"{badge(live_market, 'LIVE ZOODATA', 'SAMPLE MARKET DATA')} "
            f"<span class='subtle'>Amazon listings for “{html.escape(state['terms'])}” (search terms by the agent)</span>",
            unsafe_allow_html=True)
with_images = [r for r in listings if r.get("image")][:12]
if with_images:
    st.markdown('<div class="strip">' + "".join(
        f'<div class="thumb"><img src="{html.escape(r["image"])}" loading="lazy"/>'
        f'<div class="t">{html.escape((r.get("title") or "")[:80])}</div>'
        f'<div class="p">{money(r["price"])}</div>'
        f'<div class="s">{"★ " + str(r["rating"]) if r.get("rating") else ""}'
        f'{" · #" + format(r["bsr"], ",") + " rank" if r.get("bsr") else ""}</div>'
        f'<div class="s">{format(r["monthly_sales_floor"], ",") + "+ / mo" if r.get("monthly_sales_floor") else ""}</div></div>'
        for r in with_images) + "</div>", unsafe_allow_html=True)
else:
    st.caption("Competitor thumbnails appear with live ZooData (add ZOODATA_API_KEY to .env).")

# ---------- 4. you vs competitors ----------

st.markdown("### 4. You vs competitors")
you = state["you"]
counts = sorted(r.get("rating_count") or 0 for r in listings)
top = listings[0] if listings else {}
table = pd.DataFrame([
    {"": "Price", "You": money(you.get("price")), "Competitors (median)": money(p.get("median")),
     "Top seller": money(top.get("price"))},
    {"": "Rating", "You": str(you.get("rating") or "–"), "Competitors (median)": str(market.get("avg_rating") or "–"),
     "Top seller": str(top.get("rating") or "–")},
    {"": "Ratings count", "You": format(you.get("rating_count") or 0, ","),
     "Competitors (median)": format(counts[len(counts) // 2], ",") if counts else "–",
     "Top seller": format(top.get("rating_count") or 0, ",")},
    {"": "Best seller rank", "You": f"#{you['best_seller_rank']:,}" if you.get("best_seller_rank") else "–",
     "Competitors (median)": "–", "Top seller": f"#{top['bsr']:,}" if top.get("bsr") else "–"},
])
st.dataframe(table, hide_index=True, width="stretch")
g, r_ = st.columns(2, gap="large")
with g:
    st.markdown("#### ✅ What you're doing well")
    for item in cmp_.get("doing_well") or []:
        st.markdown(f"<div class='cmp'><b>{html.escape(item['point'])}</b>"
                    f"<span class='subtle'>{html.escape(item.get('evidence', ''))}</span></div>", unsafe_allow_html=True)
    if not cmp_.get("doing_well"):
        st.caption("Nothing stands out yet.")
with r_:
    st.markdown("#### 🔧 Room to improve")
    for item in cmp_.get("room_to_improve") or []:
        st.markdown(f"<div class='cmp'><b>{html.escape(item['point'])}</b>"
                    f"<span class='subtle'>{html.escape(item.get('evidence', ''))}</span>"
                    f"<span>→ {html.escape(item.get('action', ''))}</span></div>", unsafe_allow_html=True)
    if not cmp_.get("room_to_improve"):
        st.caption("Nothing stands out yet.")
if comp_rev:
    st.markdown(f"**Competitors' buyers praise** {chips(comp_rev.get('positives'), 'good')}", unsafe_allow_html=True)
    st.markdown(f"**Competitors' buyers complain about** {chips(comp_rev.get('issues'), 'bad')}", unsafe_allow_html=True)
st.markdown(
    badge(cmp_.get("source") == "zoowork_agent", "ZOOWORK AGENT", "FROM THE NUMBERS")
    + (" " + badge(comp_rev.get("source") == "live_zoodata", "COMPETITOR REVIEWS: ZOODATA", "COMPETITOR REVIEWS: SAMPLE")
       if comp_rev else ""),
    unsafe_allow_html=True,
)

# ---------- 5. what the agent sees ----------

st.markdown("### 5. What the agent sees")
weak = spec.get("weak_points") or []
left, right = st.columns([2, 3], gap="large")
with left:
    st.markdown(pinned_photo_html(src, weak, RED, list(LETTERS[: len(weak)])), unsafe_allow_html=True)
    st.caption((f"Showing the {stand_in} as a stand-in. " if stand_in else "") + "Pins mark approximate areas.")
with right:
    st.markdown(f"{badge(spec.get('source') == 'zoowork_agent', 'ZOOWORK AGENT', 'SAMPLE SPEC')} "
                f"&nbsp;**{html.escape(spec['title'])}**", unsafe_allow_html=True)
    st.markdown(f"<span class='subtle'>{html.escape(spec['product_type'])}</span>", unsafe_allow_html=True)
    if spec.get("materials"):
        st.markdown("**Materials:** " + ", ".join(spec["materials"]))
    if spec.get("features"):
        st.markdown("**Features:** " + " · ".join(spec["features"]))
    st.markdown("**Weak points**")
    for letter, w in zip(LETTERS, weak):
        st.markdown(f'<div class="wp"><span class="letter">{letter}</span><b>{html.escape(w["title"])}</b> '
                    f'<span class="subtle">{html.escape(w["why"])}</span></div>', unsafe_allow_html=True)
    if spec.get("summary"):
        st.markdown(f"<span class='subtle'>{html.escape(spec['summary'])}</span>", unsafe_allow_html=True)

# ---------- 6. improve it ----------

st.markdown("### 6. Improve it")
evidence: dict[str, Any] | None = state.get("evidence")
if evidence:
    st.markdown(
        f"**Backed by real reviews.** Read {evidence['n_low']} low-star (1 to 3) and {evidence['n_high']} high-star "
        f"reviews, the most helpful first, across: {', '.join(s['name'] for s in evidence['sources'])}."
    )
    ev1, ev2 = st.columns([3, 2], gap="large")
    with ev1:
        st.dataframe(
            pd.DataFrame(
                [{"Theme": t["name"], "Type": t["kind"], "Reviews": t["count"], "Share of low-star": t["share"],
                  "Avg rating": t["avg_rating"]} for t in evidence["themes"][:8]]
            ).style.format({"Share of low-star": "{:.0%}", "Avg rating": "{:.1f}"}),
            width="stretch", hide_index=True, height=318,
        )
    with ev2:
        st.markdown("**What buyers like (keep this)**")
        st.markdown("<span class='subtle'>" + html.escape(", ".join(p["name"] for p in evidence["positives"][:6]) or "n/a")
                    + "</span>", unsafe_allow_html=True)
        st.caption("Themes are ZooData's AI tags on each review. Counts are reviews mentioning the theme, computed in code. "
                   "The agent can only cite themes and reviews that exist here.")
    grounded_note = (state.get("grounded") or {}).get("note")
    if grounded_note:
        st.warning(grounded_note)
st.caption("Cost and price effects are the agent's estimates. Tick the changes to price in."
           + (" Autopilot picked the ticked ones." if auto else ""))
auto_picked = set((auto or {}).get("picked") or [])
selected: list[dict[str, Any]] = []
for i, imp in enumerate(spec["improvements"], 1):
    c1, c2 = st.columns([2, 5])
    with c1:
        default = (i in auto_picked) if auto else i <= 2
        on = st.checkbox(f"{i}. {imp['title']}", value=default, key=f"imp_{basis}_{i}_{bool(auto)}")
    with c2:
        st.markdown(
            f"<span class='subtle'>Fixes: {html.escape(imp['fixes'])} · +{money(imp['unit_cost_delta'])} cost, "
            f"+{money(imp['price_delta'])} price ({imp['confidence']} confidence). Risk: {html.escape(imp['risk'])}</span>",
            unsafe_allow_html=True,
        )
        sup = imp.get("support")
        if sup and evidence:
            st.markdown(
                f"<span class='subtle'><b>Backed by {sup['count']} of {sup['of']} low-star reviews "
                f"({sup['share']:.0%}), avg {sup['avg_rating']} stars.</b> Breaks even at "
                f"+{money(breakeven_price_lift(after_event, imp['unit_cost_delta']))} price.</span>",
                unsafe_allow_html=True,
            )
            with st.expander(f"Read the reviews behind change {i}"):
                for rid in (imp.get("review_ids") or sup["review_ids"])[:3]:
                    r = evidence["reviews"][rid]
                    st.markdown(
                        f"> {r['text']}\n>\n> *{r['rating']:.0f} stars, {r['helpful']} found helpful"
                        f"{', verified purchase' if r['verified'] else ''}, on {r['competitor']} ({rid})*"
                    )
    if on:
        selected.append({**imp, "n": i})
base_e = compute(after_event)
cost_add = sum(s["unit_cost_delta"] for s in selected)
price_add = sum(s["price_delta"] for s in selected)
improved = compute(apply_changes(after_event, cost_add, price_add))
r1, r2, r3, r4 = st.columns(4)
r1.metric("Price", money(improved["price"]), f"{money(price_add)} vs now")
r2.metric("Landed cost", money(improved["landed"]), money(improved["landed"] - base_e["landed"]), delta_color="inverse")
r3.metric("Profit / unit", money(improved["profit"]), money(improved["profit"] - base_e["profit"]))
r4.metric("Margin", f"{improved['margin']:.1%}", f"{(improved['margin'] - base_e['margin']) * 100:+.1f} pts")
if selected and improved["price"] > p["p75"]:
    st.warning(f"The new price {money(improved['price'])} is above what the top quarter of competitors charge "
               f"({money(p['p75'])}). Buyers may not accept it without strong reviews.")
after_img = annotate(jpeg, selected, "#00a374", [str(s["n"]) for s in selected], "After: requested changes")
bb1, bb2 = st.columns(2, gap="large")
with bb1:
    st.image(annotate(jpeg, weak, "#e5484d", list(LETTERS[: len(weak)]), "Before: weak points", "why"))
with bb2:
    st.image(after_img)
st.caption("A tech-pack mockup: changed areas are marked and labelled on your photo (approximate), "
           "not a re-rendered photo.")

# ---------- 7. where to source it ----------

st.markdown("### 7. Where to source it")
sourcing = spec.get("sourcing") or []
if sourcing:
    cols = st.columns(len(sourcing))
    for col, s in zip(cols, sourcing):
        q = s.get("search_terms") or spec["search_terms"]
        url = "https://www.alibaba.com/trade/search?" + urllib.parse.urlencode({"SearchText": q})
        with col:
            st.markdown(
                f'<div class="card"><h4>{html.escape(s["region"])}</h4><div>{html.escape(s["why"])}</div>'
                f'<div class="subtle" style="margin-top:6px">Watch out: {html.escape(s["watch_out"])}</div>'
                f'<div style="margin-top:8px"><a href="{html.escape(url)}" target="_blank">'
                f'Search “{html.escape(q)}” on Alibaba ↗</a></div></div>', unsafe_allow_html=True)
    if spec.get("certifications"):
        st.markdown("**Ask suppliers for:** " + " · ".join(spec["certifications"]))
    st.caption("Regions, not specific factories: we have no supplier database, so verify any supplier yourself. "
               "Check the current US duty for each country, since it changes the landed cost.")

# ---------- 8. spec sheet + hand-off ----------

st.markdown("### 8. Spec sheet for suppliers")
if not selected:
    st.caption("Tick at least one change to build the spec sheet.")
else:
    if not auto:
        # Same hand-off contract as the Product to PO page, so Supplier RFQ picks these changes up.
        st.session_state["rfq_context"] = {
            "keyword": spec["title"],
            "changes": [{k: s[k] for k in ("title", "fixes", "unit_cost_delta", "price_delta", "supplier_ask")}
                        for s in selected],
            "inputs": asdict(after_event),
            "price_add": price_add,
        }
    sheet = build_spec_sheet(jpeg, spec, selected, improved, annotated=after_img)
    c1, c2 = st.columns([1, 2])
    with c1:
        st.download_button("Download spec sheet", sheet.encode(), file_name=f"spec-sheet-{basis}.html",
                           mime="text/html", width="stretch")
        st.caption("Open it and print to PDF. Nothing is sent.")
    with c2:
        try:
            st.page_link("pages/2_Supplier_RFQ.py", label="Supplier RFQ: see the vendor chats →", icon="📦")
        except Exception:
            st.info("Open **Supplier RFQ** in the sidebar. Your selected changes carry over.")
    with st.expander("Preview the spec sheet"):
        components.html(sheet, height=620, scrolling=True)

# ---------- 9. autopilot: quotes and purchase order (stops for approval) ----------

if auto:
    st.markdown("### 9. Autopilot: quotes and purchase order")
    rfq = auto["rfq"]
    st.markdown(badge(rfq.get("mode") != "scripted", "ZOOWORK AGENTS", "SCRIPTED REPLIES")
                + ' <span class="badge badge-sample">SIMULATED VENDORS</span>', unsafe_allow_html=True)
    if rfq.get("note"):
        st.warning(rfq["note"])
    st.dataframe(pd.DataFrame([{
        "Vendor": r["name"], "Country": r["country"], "Factory $": r["unit_price"], "Landed $": round(r["landed"], 2),
        "Profit / unit $": round(r["profit"], 2), "Margin": f"{r['margin']:.1%}", "MOQ": r["moq"],
        "Lead days": r["lead_time_days"], "Meets all": "✅" if r["eligible"] else "❌",
    } for r in auto["rows"]]), hide_index=True, width="stretch")
    st.caption("Full chats on the **Supplier RFQ** page.")
    po = auto.get("po")
    if po and auto.get("approved") is None:
        st.markdown("#### ✋ Purchase order waiting for your approval")
        st.code(po_text(po), language=None)
        a1, a2, _ = st.columns([1, 1, 3])
        if a1.button("Approve purchase order", type="primary", width="stretch"):
            auto["path"], auto["approved"] = write_po(po), True
            st.rerun()
        if a2.button("Reject", width="stretch"):
            auto["approved"] = False
            st.rerun()
    elif auto.get("approved"):
        st.success(f"Approved and saved to `{auto['path']}`. Nothing was sent to the vendor.")
    elif auto.get("approved") is False:
        st.info("Purchase order rejected. Nothing was saved or sent.")
    else:
        st.info("No vendor could make this order. Adjust the changes on Supplier RFQ.")
