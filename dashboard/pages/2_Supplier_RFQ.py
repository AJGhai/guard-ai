"""Product-to-PO, Step 2: from a better product to a purchase order.

Takes the changes ticked on the Product to PO page, asks three SIMULATED vendors for quotes in
WhatsApp-style chats (ZooWork agents, or scripted replies with no credits), compares them on
landed cost, and drafts a purchase order that a person approves. Nothing is ever sent: an
approved PO is written to data/outbox/po/ and goes nowhere else.
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import style  # noqa: E402
from agent.sourcing import run_sourcing  # noqa: E402
from tools.rfq import VENDORS, build_po, buyer_target, evaluate, po_text, reference_cost, write_po  # noqa: E402
from tools.unit_economics import Inputs  # noqa: E402

GREEN, AMBER, RED, GREY, BLUE = style.accents()

DEMO_CONTEXT: dict[str, Any] = {
    "keyword": "women linen wide leg pants",
    "changes": [
        {"title": "White colorway with lining", "fixes": "see-through in white", "unit_cost_delta": 1.25,
         "price_delta": 3.0, "supplier_ask": "For the white color only, add a lightweight breathable lining and quote the extra cost."},
        {"title": "Extend sizes up to 3XL", "fixes": "runs small", "unit_cost_delta": 0.5, "price_delta": 1.0,
         "supplier_ask": "Can you make sizes up to 3XL, and what is the minimum order per size?"},
    ],
    "inputs": {"price": 29.99, "supplier_cost": 9.0, "shipping": 1.5, "duty_rate": 0.12,
               "referral_rate": 0.15, "fba_fee": 5.4, "ad_rate": 0.10},
    "price_add": 4.0,
}

st.set_page_config(page_title="Supplier quotes", page_icon=":speech_balloon:", layout="wide")
style.inject()
st.markdown(
    """
    <style>
      .chat { display:flex; flex-direction:column; gap:8px; padding: 6px 2px; }
      .msg { max-width: 80%; padding: 8px 12px; border-radius: 12px; font-size: 0.85rem; line-height: 1.4;
        color: var(--mg-text); }
      .msg .who { font-size: 0.68rem; color: var(--mg-muted); margin-bottom: 2px; letter-spacing: .04em; }
      .msg.buyer { align-self: flex-end; background: var(--mg-green-bg); border: 1px solid var(--mg-green); }
      .msg.vendor { align-self: flex-start; background: var(--mg-surface); border: 1px solid var(--mg-border); }
    </style>
    """,
    unsafe_allow_html=True,
)


def money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def bubbles(messages: list[dict[str, str]], vendor_name: str) -> str:
    out = ['<div class="chat">']
    for m in messages:
        side = "buyer" if m["role"] == "buyer" else "vendor"
        who = "Your sourcing agent" if side == "buyer" else vendor_name
        out.append(
            f'<div class="msg {side}"><div class="who">{html.escape(who)}</div>'
            f'{html.escape(m["text"]).replace(chr(10), "<br>")}</div>'
        )
    out.append("</div>")
    return "".join(out)


# ---------- context from Step 1 ----------

ctx = st.session_state.get("rfq_context")
using_demo = not ctx or not ctx.get("changes")
if using_demo:
    ctx = DEMO_CONTEXT
base = Inputs(**ctx["inputs"])
changes: list[dict[str, Any]] = ctx["changes"]
price_add: float = ctx["price_add"]
product: str = ctx["keyword"]
ref = reference_cost(base.supplier_cost, changes)

# ---------- header ----------

style.hero(
    "Supplier Quotes",
    "Three vendors quote your product changes. Compare the true cost per unit, then approve one purchase order.",
    hint="Vendors are simulated for the demo. No real message is ever sent.",
    badge_html='<span class="badge badge-sim">SIMULATED VENDORS</span>',
)
style.flow_bar(3 if st.session_state.get("po_saved") else 2)
style.glossary()

if using_demo:
    st.info(
        "Using a demo set of changes. Pick changes on **Improve my product** or **Plan a new product** first to use your own."
    )

st.markdown("### 1. What we are asking for")
c1, c2 = st.columns([3, 2])
with c1:
    st.markdown(f"**{product}**")
    for i, c in enumerate(changes, 1):
        st.markdown(
            f"{i}. **{c['title']}**: {c['fixes']}. "
            f"<span class='subtle'>+{money(c['unit_cost_delta'])} cost, +{money(c['price_delta'])} price (estimates)</span>",
            unsafe_allow_html=True,
        )
with c2:
    qty = st.number_input("First order (units)", 50, 20000, 500, 50)
    deadline = st.number_input("Needed within (days)", 7, 365, 60, 1)
    has_key = bool(os.environ.get("ZOOWORK_API_KEY", "").strip())
    use_agents = st.toggle(
        "Vendors and buyer are ZooWork agents",
        value=has_key,
        disabled=not has_key,
        help="Off: scripted replies, no credits, instant. Use it as a backup.",
    )

with st.expander("Demo assumptions (editable)"):
    st.caption(
        "Vendor prices and terms are simulated. Duty rates are assumptions for the demo, not current law. "
        "Changing a duty rate re-ranks the vendors instantly."
    )
    duty_df = st.data_editor(
        pd.DataFrame(
            {"Vendor": [v.name for v in VENDORS], "Country": [v.country for v in VENDORS],
             "Assumed duty (%)": [v.duty_rate * 100 for v in VENDORS]}
        ),
        disabled=["Vendor", "Country"],
        hide_index=True,
        width="stretch",
        key="duty_editor",
    )
duty_by_vendor = {v.key: float(duty_df.loc[i, "Assumed duty (%)"]) / 100 for i, v in enumerate(VENDORS)}

run_key = (product, tuple(c["title"] for c in changes), qty, deadline, use_agents)

# Arriving from "Continue" on the Improve page starts the quotes right away, unless this same
# request already has quotes (for example from the recorded demo).
autostart = st.session_state.pop("rfq_autostart", False)
_have = st.session_state.get("rfq_result")
auto_run = autostart and not (_have and _have["key"] == run_key)

if st.button("Request quotes from three vendors", type="primary") or auto_run:
    with st.status("Contacting vendors...", expanded=True) as status:
        def on_stage(label: str) -> None:
            status.update(label=label)
            st.write(label)

        result = run_sourcing(product, changes, ref, int(qty), int(deadline), use_agents, on_stage)
        status.update(label="Quotes are in", state="complete", expanded=False)
    st.session_state["rfq_result"] = {"key": run_key, "result": result}
    st.session_state.pop("po_saved", None)

saved = st.session_state.get("rfq_result")
if not saved or saved["key"] != run_key:
    st.caption("Press the button to start. With the agents on, it takes about 30 seconds.")
    st.stop()

result = saved["result"]
if result.get("note"):
    st.warning(result["note"])
if result["mode"] == "scripted":
    st.markdown(
        '<span class="badge badge-sample">SCRIPTED REPLIES</span> '
        '<span class="subtle">Fixed vendor profiles, no agents involved.</span>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<span class="badge badge-live">ZOOWORK AGENTS</span> '
        '<span class="subtle">Each vendor is an agent playing a supplier; a buyer-side agent negotiates.</span>',
        unsafe_allow_html=True,
    )

# ---------- 2. conversations ----------

st.markdown("### 2. The conversations")
with st.expander("Request for quote sent to every vendor"):
    st.code(result["chats"][VENDORS[0].key][0]["text"], language=None)
tabs = st.tabs([f"{v.name} ({v.country})" for v in VENDORS])
for tab, v in zip(tabs, VENDORS):
    with tab:
        st.markdown(bubbles(result["chats"][v.key][1:], v.name), unsafe_allow_html=True)

# ---------- 3. comparison ----------

rows = evaluate(result["quotes"], base, price_add, int(qty), int(deadline), duty_by_vendor)
st.markdown("### 3. Quotes compared on what you actually pay")


def tick(ok: bool) -> str:
    return "ok" if ok else "NO"


table = pd.DataFrame(
    [
        {
            "Vendor": r["name"],
            "Country": r["country"],
            "Factory price $": r["unit_price"],
            "Duty": r["duty_rate"],
            "Landed $": r["landed"],
            "Profit / unit $": r["profit"],
            "Margin": r["margin"],
            "MOQ": r["moq"],
            "Lead (days)": r["lead_time_days"],
            "MOQ fits": tick(r["flags"]["moq_ok"]),
            "On time": tick(r["flags"]["lead_ok"]),
            "All changes": tick(r["flags"]["changes_ok"]),
            "Certifications": ", ".join(r["certifications"]) or "none stated",
            "Payment": r["payment_terms"],
            "Price in chat": "yes" if r.get("verified", True) else "CHECK",
        }
        for r in rows
    ]
)


def style_flag(v: str) -> str:
    return f"color: {GREEN}" if v in ("ok", "yes") else f"color: {RED}; font-weight: 700"


styled = table.style.map(style_flag, subset=["MOQ fits", "On time", "All changes", "Price in chat"]).format(
    {"Factory price $": "{:.2f}", "Duty": "{:.0%}", "Landed $": "{:.2f}", "Profit / unit $": "{:.2f}",
     "Margin": "{:.1%}", "MOQ": "{:,}"}
)
st.dataframe(styled, width="stretch", hide_index=True)
st.caption(
    "Landed = factory price + shipping + duty. Duty rates are your assumptions above. "
    "'Price in chat' checks that the extracted price really appears in the vendor's words."
)

best = rows[0]
cheapest_fob = min(rows, key=lambda r: r["unit_price"])
if best["eligible"]:
    msg = (
        f"**Recommended: {best['name']} ({best['country']}).** Profit {money(best['profit'])} per unit "
        f"({best['margin']:.1%} margin), MOQ and lead time fit, and it can make every change."
    )
    st.success(msg)
else:
    failing = [k for k, ok in best["flags"].items() if not ok]
    st.warning(
        f"**No vendor meets every requirement.** Best partial match: {best['name']}, "
        f"which fails: {', '.join(failing)}. Adjust the quantity or deadline, or negotiate further."
    )
if cheapest_fob["vendor"] != best["vendor"]:
    st.markdown(
        f"<span class='subtle'>Cheapest factory price is {cheapest_fob['name']} at {money(cheapest_fob['unit_price'])}, "
        f"but after duty and the order requirements it earns {money(cheapest_fob['profit'])} per unit "
        f"against {money(best['profit'])} for {best['name']}.</span>",
        unsafe_allow_html=True,
    )
if result.get("summary"):
    st.markdown(
        "<span class='subtle'>Agent's read of the chats, written before duty and your order size are counted, "
        "so it can pick a different vendor. The recommendation above is the one to use: "
        f"{html.escape(result['summary'])}</span>",
        unsafe_allow_html=True,
    )

# ---------- 4. purchase order ----------

st.markdown("### 4. Approve the order")
st.caption("Pick the vendor, read the draft below, then approve. Approving only saves a file; nothing is sent.")
choice = st.selectbox("Order from", [r["name"] for r in rows], index=0)
row = next(r for r in rows if r["name"] == choice)
if not row["eligible"]:
    st.warning(f"{row['name']} does not meet every requirement. Check the table before approving.")
po = build_po(row, product, changes, int(qty))
st.code(po_text(po), language=None)

if st.button("Approve and save to outbox", type="primary"):
    path = write_po(po)
    st.session_state["po_saved"] = path
if path := st.session_state.get("po_saved"):
    st.success(f"Approved and saved to `{path}`. Nothing was sent to the vendor.")
    style.next_link("pages/3_MarginGuard.py", "Last stop: protect your margin from tariffs", "🛡️")
