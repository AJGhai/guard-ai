"""Start here: one flow, from a listing to an approved order."""

from __future__ import annotations

import streamlit as st

import style

st.set_page_config(page_title="Guard AI", page_icon=":shield:", layout="wide")
style.inject()

style.hero(
    "Guard AI",
    "An AI co-pilot for small Amazon sellers. Paste your listing, see what real buyers complain about, pick the "
    "changes worth making, get supplier quotes for them, and approve an order.",
    hint="You approve every money step. Nothing is ever sent to a supplier or changed on Amazon.",
)

style.flow_bar(1)

st.markdown("### Start")
with st.container(border=True):
    st.markdown("**🔍 Improve my product**")
    st.caption("Paste your Amazon listing (or pick an example). See where you beat competitors, where you fall "
               "behind, and which changes are backed by real reviews. Then continue to supplier quotes.")
    style.next_link("pages/0_Improve_My_Product.py", "Start with your listing", "➡️")
