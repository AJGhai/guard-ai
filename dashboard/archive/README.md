# Archived pages

These pages are not in the menu. The demo is one flow: Start here, Improve my product, Supplier quotes.

- `plan_new_product.py`: market picture, unit economics and a cost event for a product you are planning.
- `tariff_protection.py`: the MarginGuard tariff pricing agent (needs `uvicorn api.server:app --port 8000`).

To bring one back, add an `st.Page("archive/<file>.py", ...)` entry in `dashboard/app.py`.

Also archived when the app became one page:

- `start_page.py`: the old Start page.
- `supplier_quotes_page.py`: Supplier quotes as its own page. The same logic now lives in
  `dashboard/quotes_section.py` and renders at the bottom of the main page.
