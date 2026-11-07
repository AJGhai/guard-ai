"""Supplier quotes, as the last section of the one-page flow (no separate page).

render() takes the changes the seller ticked (st.session_state["rfq_context"]), asks SIMULATED vendors
for quotes in WhatsApp-style chats (ZooWork agents, or scripted replies with no credits), compares them
on landed cost, and prepares the order for a person to approve, together with the message that goes to
the supplier. Nothing is ever sent: an approved order and message are written to data/outbox/ only.

Who is asked:
  - the seller's own vendors (st.session_state["vendors_mine"], from their sheet) are messaged
    automatically;
  - suggested additional vendors (the built-in sample vendors not already in the sheet) are only asked
    after the seller ticks them (checkbox key "sg_<vendor key>"). That tick is the sign-off;
  - with no sheet loaded, the three built-in sample vendors are used as before.

Session keys: rfq_context, rfq_result, rfq_autostart, rfq_scroll, po_saved, rfq_po_number,
vendors_mine, vendors_source.
"""

from __future__ import annotations

import hashlib
import html
import os
from dataclasses import replace
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import style
from agent.sourcing import run_sourcing
from tools.rfq import (
    VENDORS, Vendor, build_po, build_rfq, evaluate, po_text, reference_cost, vendor_message, write_message, write_po,
)
from tools.unit_economics import Inputs

_CHAT_CSS = """
<style>
  .chat { display:flex; flex-direction:column; gap:8px; padding: 6px 2px; }
  .msg { max-width: 80%; padding: 8px 12px; border-radius: 12px; font-size: 0.85rem; line-height: 1.4;
    color: var(--mg-text); }
  .msg .who { font-size: 0.68rem; color: var(--mg-muted); margin-bottom: 2px; letter-spacing: .04em; }
  .msg.buyer { align-self: flex-end; background: var(--mg-green-bg); border: 1px solid var(--mg-green); }
  .msg.vendor { align-self: flex-start; background: var(--mg-surface); border: 1px solid var(--mg-border); }
</style>
"""


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


def _tick(ok: bool) -> str:
    return "ok" if ok else "NO"


# ---------- who gets asked ----------

def suggested_pool(mine: list[Vendor]) -> list[Vendor]:
    """Additional vendors we could suggest: the built-in ones that are not already in the seller's sheet."""
    names = {v.name.lower() for v in mine}
    return [replace(v, source="suggested") for v in VENDORS if v.name.lower() not in names]


def active_vendors() -> list[Vendor]:
    """The seller's own vendors, plus any suggested ones they have ticked. Default: the built-in three."""
    mine: list[Vendor] = list(st.session_state.get("vendors_mine") or [])
    if not mine:
        return list(VENDORS)
    approved = [v for v in suggested_pool(mine) if st.session_state.get(f"sg_{v.key}")]
    return mine + approved


def render() -> None:
    ctx = st.session_state.get("rfq_context")
    st.markdown('<div id="quotes-anchor"></div>', unsafe_allow_html=True)
    if st.session_state.pop("rfq_scroll", False):
        components.html(
            "<script>const el = window.parent.document.getElementById('quotes-anchor');"
            "if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); }</script>",
            height=0,
        )
    st.markdown("## Step 2: Supplier quotes")
    if not ctx or not ctx.get("changes"):
        st.info("Tick at least one change in step 1, then continue here.")
        return

    GREEN, AMBER, RED, GREY, BLUE = style.accents()
    st.markdown(_CHAT_CSS, unsafe_allow_html=True)
    base = Inputs(**ctx["inputs"])
    changes: list[dict[str, Any]] = ctx["changes"]
    price_add: float = ctx["price_add"]
    product: str = ctx["keyword"]
    ref = reference_cost(base.supplier_cost, changes)

    st.markdown(
        '<span class="badge badge-sim">SIMULATED VENDORS</span> '
        '<span class="subtle">Replies are simulated from vendor terms. Compare the true cost per unit, then approve '
        "one order and its message. No real message is ever sent.</span>",
        unsafe_allow_html=True,
    )

    st.markdown("### What we are asking for")
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
        qty = st.number_input("First order (units)", 50, 20000, 500, 50, key="rfq_qty")
        deadline = st.number_input("Needed within (days)", 7, 365, 60, 1, key="rfq_deadline")
        has_key = bool(os.environ.get("ZOOWORK_API_KEY", "").strip())
        use_agents = st.toggle(
            "Vendors and buyer are ZooWork agents", value=has_key, disabled=not has_key, key="rfq_agents",
            help="Off: scripted replies, no credits, instant. Use it as a backup.",
        )

    # ---------- who to ask ----------
    mine: list[Vendor] = list(st.session_state.get("vendors_mine") or [])
    st.markdown("### Who to ask")
    if mine:
        st.markdown(
            f"**Your vendors, messaged automatically** ({st.session_state.get('vendors_source', 'your sheet')}): "
            + ", ".join(f"{v.name} ({v.country})" for v in mine)
        )
        pool = suggested_pool(mine)
        with st.container(border=True):
            st.markdown("**Suggested additional vendors.** Not in your sheet, so nobody is contacted until you "
                        "tick them. Ticking is your sign-off.")
            for v in pool:
                certs = ", ".join(v.certs) or "no certifications listed"
                st.checkbox(
                    f"{v.name} ({v.country}): minimum {v.moq} units, {v.lead_days} days, {certs}",
                    key=f"sg_{v.key}",
                )
    else:
        st.caption("Using three sample vendors. Load a vendor sheet at the top of the page to use your own, "
                   "and approve any extra vendors here.")
    vendors = active_vendors()

    with st.expander("Demo assumptions (editable)"):
        st.caption(
            "Vendor prices and terms are simulated. Duty rates are assumptions for the demo, not current law. "
            "Changing a duty rate re-ranks the vendors instantly."
        )
        duty_df = st.data_editor(
            pd.DataFrame(
                {"Vendor": [v.name for v in vendors], "Country": [v.country for v in vendors],
                 "Assumed duty (%)": [v.duty_rate * 100 for v in vendors]}
            ),
            disabled=["Vendor", "Country"], hide_index=True, width="stretch",
            key="duty_editor_" + "_".join(v.key for v in vendors),
        )
    duty_by_vendor = {v.key: float(duty_df.loc[i, "Assumed duty (%)"]) / 100 for i, v in enumerate(vendors)}

    run_key: tuple = (product, tuple(c["title"] for c in changes), qty, deadline, use_agents)
    if mine:  # custom vendor set: the quotes belong to exactly these vendors
        run_key += (tuple(v.key for v in vendors),)

    # ---------- the messages to the vendors: shown, and editable, before they go out ----------
    auto_send = bool(st.session_state.get("auto_send"))
    st.markdown("### Messages to your vendors")
    st.caption(
        "Fully automatic is on: these messages go out as written, no review." if auto_send
        else "Read and edit each message. Nothing goes out until you approve below. (Prefer no review? Turn on "
             "**Fully automatic** at the top of the page.)"
    )
    drafts = {v.key: build_rfq(product, changes, int(qty), int(deadline), vendor_name=v.name) for v in vendors}
    rfq_texts: dict[str, str] = {}
    msg_tabs = st.tabs([v.name for v in vendors])
    for tab, v in zip(msg_tabs, vendors):
        with tab:
            if v.contact:
                st.caption(f"To: {v.contact}")
            digest = hashlib.md5(drafts[v.key].encode()).hexdigest()[:8]   # a new draft resets the box
            rfq_texts[v.key] = st.text_area(
                "Message", value=drafts[v.key], height=280, label_visibility="collapsed",
                key=f"rfq_draft_{v.key}_{digest}",
            )

    # Arriving from "Continue" with Fully automatic on starts the quotes right away, unless this same request
    # already has quotes (for example from the recorded demo).
    autostart = st.session_state.pop("rfq_autostart", False)
    have = st.session_state.get("rfq_result")
    auto_run = autostart and not (have and have["key"] == run_key)

    n_new = len([v for v in vendors if v.source == "suggested"])
    label = f"Approve and send to {len(vendors)} vendors (simulated)" + (f", incl. {n_new} you approved" if n_new else "")
    if st.button(label, type="primary", key="rfq_go") or auto_run:
        with st.status("Contacting vendors...", expanded=True) as status:
            def on_stage(text: str) -> None:
                status.update(label=text)
                st.write(text)

            result = run_sourcing(product, changes, ref, int(qty), int(deadline), use_agents, on_stage, vendors,
                                  rfq_texts)
            status.update(label="Quotes are in", state="complete", expanded=False)
        st.session_state["rfq_result"] = {"key": run_key, "result": result}
        st.session_state.pop("po_saved", None)
        st.session_state.pop("rfq_po_number", None)

    saved = st.session_state.get("rfq_result")
    if not saved or saved["key"] != run_key:
        st.caption("Press the button to send. Vendors you ticked above are contacted only when you press it. "
                   "With the agents on, the replies take about 30 seconds.")
        return

    result = saved["result"]
    if result.get("note"):
        st.warning(result["note"])
    if result["mode"] == "scripted":
        st.markdown(
            '<span class="badge badge-sample">SCRIPTED REPLIES</span> '
            '<span class="subtle">Fixed vendor terms, no agents involved.</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<span class="badge badge-live">ZOOWORK AGENTS</span> '
            '<span class="subtle">Each vendor is an agent playing a supplier; a buyer-side agent negotiates.</span>',
            unsafe_allow_html=True,
        )

    st.markdown("### The conversations")
    first_key = next(iter(result["chats"]))
    with st.expander("Request for quote sent to every vendor"):
        st.code(result["chats"][first_key][0]["text"], language=None)
    quoted = [v for v in vendors if v.key in result["chats"]]
    tabs = st.tabs([f"{v.name} ({v.country})" for v in quoted])
    for tab, v in zip(tabs, quoted):
        with tab:
            st.markdown(bubbles(result["chats"][v.key][1:], v.name), unsafe_allow_html=True)

    rows = evaluate(result["quotes"], base, price_add, int(qty), int(deadline), duty_by_vendor, vendors=vendors)
    st.markdown("### Quotes compared on what you actually pay")
    table = pd.DataFrame(
        [
            {
                "Vendor": r["name"], "Country": r["country"], "Factory price $": r["unit_price"],
                "Duty": r["duty_rate"], "Landed $": r["landed"], "Profit / unit $": r["profit"],
                "Margin": r["margin"], "MOQ": r["moq"], "Lead (days)": r["lead_time_days"],
                "MOQ fits": _tick(r["flags"]["moq_ok"]), "On time": _tick(r["flags"]["lead_ok"]),
                "All changes": _tick(r["flags"]["changes_ok"]),
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
        st.success(
            f"**Recommended: {best['name']} ({best['country']}).** Profit {money(best['profit'])} per unit "
            f"({best['margin']:.1%} margin), MOQ and lead time fit, and it can make every change."
        )
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

    # ---------- approve: the order and the message to the supplier ----------
    st.markdown("### Approve the order and the message")
    st.caption("Pick the vendor, read the order and the message that goes with it, edit the message if you like, "
               "then approve. Approving saves both to the outbox; nothing is sent.")
    choice = st.selectbox("Order from", [r["name"] for r in rows], index=0, key="rfq_vendor")
    row = next(r for r in rows if r["name"] == choice)
    chosen = next((v for v in vendors if v.name == choice), None)
    if not row["eligible"]:
        st.warning(f"{row['name']} does not meet every requirement. Check the table before approving.")
    if chosen is not None and chosen.source == "suggested":
        st.info("This vendor is one you added from the suggestions.")
    po = build_po(row, product, changes, int(qty))
    po["po_number"] = st.session_state.setdefault("rfq_po_number", po["po_number"])  # stable across reruns
    st.markdown("**Purchase order**")
    st.code(po_text(po), language=None)
    contact = chosen.contact if chosen else ""
    st.markdown("**Message to the supplier**" + (f" (to {html.escape(contact)})" if contact else ""))
    message = st.text_area(
        "Message", value=vendor_message(po, contact), height=330, label_visibility="collapsed",
        key=f"rfq_msg_{po['po_number']}_{row['vendor']}_{int(qty)}_{row['unit_price']}",
    )
    a1, a2 = st.columns([2, 1])
    with a1:
        if st.button("Approve order and message the supplier (saved to outbox)", type="primary", key="rfq_approve"):
            st.session_state["po_saved"] = {"po": write_po(po), "message": write_message(po, message)}
    with a2:
        st.download_button("Download the message", message.encode("utf-8"), file_name=f"{po['po_number']}-message.txt",
                           width="stretch")
    saved_paths = st.session_state.get("po_saved")
    if saved_paths:
        if isinstance(saved_paths, dict):
            st.success(f"Approved. Order saved to `{saved_paths['po']}` and message saved to `{saved_paths['message']}`. "
                       "Nothing was sent to the vendor.")
        else:  # older sessions stored just the PO path
            st.success(f"Approved and saved to `{saved_paths}`. Nothing was sent to the vendor.")
