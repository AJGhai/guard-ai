"""Guard AI dashboard: the navigation menu. Run it from the repo root:

    .venv/bin/streamlit run dashboard/app.py

This file only builds the sidebar menu. Each page is its own file. The demo is one flow:
Start here, Improve my product, then Supplier quotes. Older pages live in dashboard/archive
(see its README) and are not in the menu.
"""

from __future__ import annotations

import streamlit as st

pages = {
    "Start": [
        st.Page("home.py", title="Start here", icon="🏠", default=True),
    ],
    "Build a better product": [
        st.Page("pages/0_Improve_My_Product.py", title="Improve my product", icon="🔍", url_path="improve"),
        st.Page("pages/2_Supplier_RFQ.py", title="Supplier quotes", icon="📦", url_path="quotes"),
    ],
}

st.navigation(pages).run()
