"""Shared look for the dashboard pages (dark finance-terminal theme)."""

from __future__ import annotations

import streamlit as st

GREEN, AMBER, RED, GREY, BLUE = "#00d395", "#f5a623", "#ff5c6c", "#6b7785", "#5b9cf5"

_CSS = f"""
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
  .card {{ background: #111823; border: 1px solid #1e2a3a; border-radius: 8px; padding: 14px 16px;
    height: 100%; }}
  .card h4 {{ margin: 0 0 6px 0; font-size: 0.95rem; }}
  .pin {{ position: absolute; transform: translate(-50%, -50%); background: {GREEN}; color: #04130d;
    border-radius: 50%; width: 26px; height: 26px; display: flex; align-items: center;
    justify-content: center; font-weight: 700; font-size: 0.8rem; border: 2px solid #ffffff; }}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
