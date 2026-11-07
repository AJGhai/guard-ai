"""MargeAI: one page, one flow. Run it from the repo root:

    .venv/bin/streamlit run dashboard/app.py

Paste an Amazon listing (or pick an example), see what real buyers say, tick the changes worth
making, then continue to supplier quotes and approve an order, all on this page. The menu is hidden;
the cost assumptions are in the collapsed side panel. Older pages (Start, Supplier quotes as a
page, Plan a new product, Tariff protection) live in dashboard/archive and are not in the app.
"""

from __future__ import annotations

import streamlit as st

st.navigation(
    [st.Page("pages/0_Improve_My_Product.py", title="MargeAI", icon="🛡️", default=True)],
    position="hidden",
).run()
