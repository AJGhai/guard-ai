"""Start here: where to go and a suggested demo order."""

from __future__ import annotations

import streamlit as st

import style

st.set_page_config(page_title="Guard AI", page_icon=":shield:", layout="wide")
style.inject()

style.hero(
    "Guard AI",
    "An AI co-pilot for small Amazon sellers. Find what to improve, get supplier quotes for it, and protect "
    "your profit when a tariff raises your costs.",
    hint="You approve every money step. Nothing is ever sent to a supplier or changed on Amazon.",
)

# ---------- 1. where to go ----------

st.markdown("### What do you want to do?")
cards = [
    ("🔍", "I already sell on Amazon", "Paste your listing. See where you beat competitors, where you fall behind, "
     "and which changes are worth making.", "pages/0_Improve_My_Product.py", "Improve my product"),
    ("💡", "I'm planning a new product", "Check the market, see what each unit really earns, and test what a cost "
     "change does to your profit.", "pages/1_Product_to_PO.py", "Plan a new product"),
    ("🛡️", "A tariff just raised my costs", "See how much margin you would lose and approve price changes "
     "that win it back.", "pages/3_MarginGuard.py", "Protect my margin"),
]
for col, (icon, title, text, page, label) in zip(st.columns(3, gap="medium"), cards):
    with col, st.container(border=True):
        st.markdown(f"**{icon} {title}**")
        st.caption(text)
        style.next_link(page, label, "➡️")
