"""Guard AI dashboard: the navigation menu. Run it from the repo root:

    .venv/bin/streamlit run dashboard/app.py

This file only builds the sidebar menu. Each page is its own file, so a teammate can work on one page
without touching the others. For the MarginGuard page, start the API first in a second terminal:

    .venv/bin/uvicorn api.server:app --port 8000
"""

from __future__ import annotations

import streamlit as st

pages = {
    "Start": [
        st.Page("home.py", title="Start here", icon="🏠", default=True),
    ],
    "Build a better product": [
        st.Page("pages/0_Improve_My_Product.py", title="Improve my product", icon="🔍", url_path="improve"),
        st.Page("pages/1_Product_to_PO.py", title="Plan a new product", icon="💡", url_path="plan"),
        st.Page("pages/2_Supplier_RFQ.py", title="Supplier quotes", icon="📦", url_path="quotes"),
    ],
    "Protect your margin": [
        st.Page("pages/3_MarginGuard.py", title="Tariff protection", icon="🛡️", url_path="tariff"),
    ],
}

st.navigation(pages).run()
