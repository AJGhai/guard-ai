# Archived pages

These pages are not in the menu. The demo is one flow: Start here, Improve my product, Supplier quotes.

- `plan_new_product.py`: market picture, unit economics and a cost event for a product you are planning.
- `tariff_protection.py`: the MarginGuard tariff pricing agent (needs `uvicorn api.server:app --port 8000`).

To bring one back, add an `st.Page("archive/<file>.py", ...)` entry in `dashboard/app.py`.
