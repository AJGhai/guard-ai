"""MarginGuard dashboard: tariff scenario -> pricing decisions -> human approval.

Run from the repo root:
    .venv\\Scripts\\streamlit run dashboard/app.py

Uses the same tools as agent/run.py (margins, comparables, pricing rules) and writes approved
changes to data/store_prices.json. It makes no ZooWork calls; live ZooData comparables are
optional (needs ZOODATA_API_KEY in .env, costs about 1 credit per page).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from tools.calc_margins import calc_margins, load_cost_sheet  # noqa: E402
from tools.comparables import DEFAULT_PRICE_MIN, get_comparables  # noqa: E402
from tools.pricing_rules import REVIEW_MARGIN, evaluate_pricing_rules  # noqa: E402

PRICES = ROOT / "data" / "store_prices.json"
PRICES_ORIGINAL = ROOT / "data" / "store_prices.original.json"

GREEN, AMBER, RED, GREY = "#00d395", "#f5a623", "#ff5c6c", "#6b7785"
ACTION_COLORS = {"REPRICE": GREEN, "ABSORB": GREY, "REVIEW": RED}

st.set_page_config(page_title="MarginGuard", page_icon=":shield:", layout="wide")
st.markdown(
    f"""
    <style>
      .block-container {{ padding-top: 2rem; max-width: 1400px; }}
      div[data-testid="stMetric"] {{
        background: #111823; border: 1px solid #1e2a3a; border-radius: 8px; padding: 14px 16px;
      }}
      div[data-testid="stMetricLabel"] p {{ color: {GREY}; text-transform: uppercase;
        font-size: 0.68rem; letter-spacing: 0.05em; }}
      div[data-testid="stMetricValue"] {{ font-size: 1.7rem; }}
      div[data-testid="stMetricDelta"] {{ font-size: 0.75rem; }}
      .badge {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.75rem;
        font-weight: 600; letter-spacing: 0.04em; }}
      .badge-live {{ background: rgba(0,211,149,.15); color: {GREEN}; border: 1px solid {GREEN}; }}
      .badge-sample {{ background: rgba(245,166,35,.12); color: {AMBER}; border: 1px solid {AMBER}; }}
      .subtle {{ color: {GREY}; font-size: 0.85rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- data helpers ----------

def pct(x: float) -> str:
    return f"{x:.1%}"


def money(x: float) -> str:
    return f"${x:,.0f}"


def has_zoodata_key() -> bool:
    return bool(os.environ.get("ZOODATA_API_KEY", "").strip())


@st.cache_data(ttl=600, show_spinner="Fetching comparables...")
def fetch_comps(mode: str, price_min: float, categories: tuple[str, ...]) -> dict[str, Any]:
    os.environ["COMPARABLES_MODE"] = mode
    os.environ["COMPARABLES_PRICE_MIN"] = str(price_min)
    return {c: get_comparables({"category_query": c}) for c in categories}


def apply_changes(changes: list[tuple[str, float]]) -> None:
    prices = json.loads(PRICES.read_text())
    for sku, new_price in changes:
        prices[sku] = new_price
    PRICES.write_text(json.dumps(prices, indent=2) + "\n")
    st.session_state["flash"] = f"Applied {len(changes)} price change(s) to data/store_prices.json."


def reset_prices() -> None:
    shutil.copyfile(PRICES_ORIGINAL, PRICES)
    st.session_state["flash"] = "Demo prices reset to the original store prices."


def select_all(keys: list[str], value: bool) -> None:
    for key in keys:
        st.session_state[key] = value


# ---------- sidebar: scenario ----------

with st.sidebar:
    st.markdown("### Tariff scenario")
    duty_old = st.slider("Current duty", 0, 100, 12, format="%d%%") / 100
    duty_new = st.slider("New duty", 0, 100, 37, format="%d%%") / 100
    volume_drop = st.slider("Volume drop on repriced items", 0, 20, 2, format="%d%%") / 100

    st.markdown("### Market data")
    live_available = has_zoodata_key()
    source_choice = st.radio(
        "Comparable prices",
        ["Curated sample", "Live ZooData"],
        index=0,
        help="Curated: premium-tier sample in market/fallback_prices.json. "
        "Live: ZooData best sellers on Amazon (about 3 credits per category).",
    )
    live_wanted = source_choice == "Live ZooData"
    if live_wanted and not live_available:
        st.warning("No ZOODATA_API_KEY in .env, using the curated sample.")
    price_min = st.number_input(
        "Live price floor ($)", 0.0, 500.0, float(DEFAULT_PRICE_MIN), 5.0, disabled=not live_wanted
    )

    st.markdown("### Demo")
    st.button("Reset store prices", on_click=reset_prices, width="stretch")

# ---------- compute ----------

rows = load_cost_sheet()
categories = tuple(sorted({r["category"] for r in rows}))
mode = "live" if (live_wanted and live_available) else "fallback"
comps = fetch_comps(mode, float(price_min), categories)

broken = [c for c, r in comps.items() if "error" in r]
if broken:
    st.error(f"No comparables for: {', '.join(broken)}")
    st.stop()

medians = {c: r["median"] for c, r in comps.items()}
rules = evaluate_pricing_rules({"medians": medians, "duty_old": duty_old, "duty_new": duty_new})
decisions: list[dict[str, Any]] = rules["decisions"]

base = calc_margins(
    {"duty_old": duty_old, "duty_new": duty_new, "prices": {}, "volume_drop": volume_drop}
)

reprice = [d for d in decisions if d["action"] == "REPRICE"]
keys = {d["sku"]: f"ok_{d['sku']}_{d['new_price']}" for d in reprice}
selected = [d for d in reprice if st.session_state.get(keys[d["sku"]], False)]

after = calc_margins(
    {
        "duty_old": duty_old,
        "duty_new": duty_new,
        "prices": {d["sku"]: d["new_price"] for d in selected},
        "volume_drop": volume_drop,
    }
)

units = {r["sku"]: r["units_per_month"] for r in rows}
today_profit = sum(units[s] * (v["price_old"] - v["landed_old"]) for s, v in base["skus"].items())
today_revenue = sum(units[s] * v["price_old"] for s, v in base["skus"].items())
margin_today = today_profit / today_revenue if today_revenue else 0.0
margin_tariff = base["totals"]["blended_margin"]
margin_after = after["totals"]["blended_margin"]

# ---------- header ----------

left, right = st.columns([3, 2])
with left:
    st.markdown("## MarginGuard")
    st.markdown(
        f'<span class="subtle">Tariff-aware pricing for fashion merchants. Duty '
        f"{duty_old:.0%} to {duty_new:.0%}. A person approves every price change.</span>",
        unsafe_allow_html=True,
    )
with right:
    is_live = all(r["data_source"] == "live_zoodata" for r in comps.values())
    if is_live:
        detail = next(iter(comps.values()))["source_detail"]
        badge = f'<span class="badge badge-live">LIVE ZOODATA</span> <span class="subtle">{detail}</span>'
    else:
        badge = '<span class="badge badge-sample">CURATED SAMPLE</span> <span class="subtle">market/fallback_prices.json</span>'
        if live_wanted and live_available:
            badge += '<br><span class="subtle">Live data was unavailable or too thin, so the sample was used.</span>'
    st.markdown(f"<div style='text-align:right'>{badge}</div>", unsafe_allow_html=True)

if flash := st.session_state.pop("flash", None):
    st.success(flash)

# ---------- KPIs ----------

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Duty exposure / mo", money(base["totals"]["exposure"]))
k2.metric("Margin today", pct(margin_today))
k3.metric("Margin, tariff", pct(margin_tariff), f"{(margin_tariff - margin_today) * 100:+.1f} pts vs today")
k4.metric(
    "Margin, approved",
    pct(margin_after),
    f"{(margin_after - margin_tariff) * 100:+.1f} pts vs tariff",
)
k5.metric("Recovery / mo", money(after["totals"]["recovery"]), f"{len(selected)} of {len(reprice)} changes")

# ---------- decisions table ----------

st.markdown("### Pricing decisions")
table = pd.DataFrame(
    [
        {
            "SKU": d["sku"],
            "Product": d["name"],
            "Action": d["action"],
            "Current $": d["current_price"],
            "Proposed $": d["new_price"],
            "Market median $": d["median"],
            "Margin today": base["skus"][d["sku"]]["margin_old"],
            "Margin under tariff": d["tariff_margin"],
            "Margin after": after["skus"][d["sku"]]["margin_new"],
            "Why": d["reason"],
        }
        for d in decisions
    ]
)


def style_action(value: str) -> str:
    return f"color: {ACTION_COLORS.get(value, GREY)}; font-weight: 700"


styled = table.style.map(style_action, subset=["Action"]).format(
    {
        "Current $": "{:,.0f}",
        "Proposed $": "{:,.0f}",
        "Market median $": "{:,.2f}",
        "Margin today": "{:.1%}",
        "Margin under tariff": "{:.1%}",
        "Margin after": "{:.1%}",
    }
)
st.dataframe(styled, width="stretch", hide_index=True, height=392)

# ---------- approvals ----------

st.markdown("### Approvals")
if not reprice:
    st.info("No price increases proposed for this scenario.")
else:
    st.caption("Nothing is written until you apply. Tick the changes you approve.")
    all_keys = list(keys.values())
    b1, b2, _ = st.columns([1, 1, 4])
    b1.button("Select all", on_click=select_all, args=(all_keys, True), width="stretch")
    b2.button("Clear", on_click=select_all, args=(all_keys, False), width="stretch")
    for d in reprice:
        increase = d["new_price"] / d["current_price"] - 1
        c1, c2 = st.columns([2, 5])
        c1.checkbox(
            f"{d['sku']}  ${d['current_price']:g} to ${d['new_price']:g} ({increase:+.0%})",
            key=keys[d["sku"]],
        )
        c2.markdown(f'<span class="subtle">{d["name"]}. {d["reason"]}</span>', unsafe_allow_html=True)
    st.button(
        f"Apply {len(selected)} approved change(s)",
        type="primary",
        disabled=not selected,
        on_click=apply_changes,
        args=([(d["sku"], d["new_price"]) for d in selected],),
    )

review = [d for d in decisions if d["action"] == "REVIEW"]
if review:
    st.markdown("### Flagged for sourcing review")
    st.caption(f"Margin under the tariff is below {REVIEW_MARGIN:.0%}, so these are not repriced.")
    for d in review:
        st.markdown(f"- **{d['sku']} {d['name']}**: tariff margin {pct(d['tariff_margin'])}")

# ---------- chart ----------

st.markdown("### Margin by product")
chart_df = pd.DataFrame(
    [
        {"SKU": s, "Stage": stage, "Margin": value}
        for s, v in base["skus"].items()
        for stage, value in (
            ("Today", v["margin_old"]),
            ("Under tariff", v["margin_new"]),
            ("After approved", after["skus"][s]["margin_new"]),
        )
    ]
)
bars = (
    alt.Chart(chart_df)
    .mark_bar()
    .encode(
        x=alt.X("SKU:N", title=None, sort=None),
        xOffset="Stage:N",
        y=alt.Y("Margin:Q", axis=alt.Axis(format="%"), title=None),
        color=alt.Color(
            "Stage:N",
            scale=alt.Scale(domain=["Today", "Under tariff", "After approved"], range=[GREY, RED, GREEN]),
            legend=alt.Legend(orient="top", title=None),
        ),
        tooltip=["SKU", "Stage", alt.Tooltip("Margin:Q", format=".1%")],
    )
)
threshold = alt.Chart(pd.DataFrame({"y": [REVIEW_MARGIN]})).mark_rule(
    color=AMBER, strokeDash=[4, 4]
).encode(y="y:Q")
st.altair_chart((bars + threshold).properties(height=320), width="stretch")
st.caption(f"Dashed line: {REVIEW_MARGIN:.0%} sourcing-review threshold.")

# ---------- comparables detail ----------

with st.expander("Comparables detail"):
    detail_rows = []
    for category, r in comps.items():
        detail_rows.append(
            {
                "Category": category,
                "Source": r["data_source"],
                "Listings": r["n"],
                "Low $": r["low"],
                "Median $": r["median"],
                "75th pct $": r.get("p75"),
                "High $": r["high"],
                "Curated median $": r.get("sample_median", r["median"] if r["data_source"] != "live_zoodata" else None),
            }
        )
    st.dataframe(pd.DataFrame(detail_rows), width="stretch", hide_index=True)
