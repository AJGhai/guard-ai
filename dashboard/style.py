"""Shared look for the dashboard pages: one palette, a light and a dark version.

The theme (light or dark) is chosen in Streamlit's own menu (top right, Settings > Theme), or follows the
computer's setting. Colours live in CSS variables (--mg-*) that inject() sets for the active theme, so
page styles use var(--mg-surface) and never a fixed hex. Charts and tables cannot read CSS variables,
so accents() returns the same colours as plain strings.
"""

from __future__ import annotations

import streamlit as st

# Dark-theme accents, kept as module constants for older imports. Pages should call accents() instead,
# which returns the right colours for the active theme.
GREEN, AMBER, RED, GREY, BLUE = "#00d395", "#f5a623", "#ff5c6c", "#8b97a5", "#5b9cf5"

DARK = {
    "surface": "#111823", "border": "#26334a", "text": "#e6edf3", "text2": "#c9d1d9", "muted": "#8b97a5",
    "green": "#00d395", "amber": "#f5a623", "red": "#ff5c6c", "blue": "#5b9cf5", "on_accent": "#04130d",
}
# MargeAI brand: light yellow and blue. "green" is kept for success / live states only.
LIGHT = {
    "surface": "#FFF4C9", "border": "#E9D78C", "text": "#14284B", "text2": "#2B3F66", "muted": "#5B6785",
    "green": "#1E7B55", "amber": "#A85F00", "red": "#C4283A", "blue": "#1F5FBF", "on_accent": "#FFFFFF",
    "yellow": "#FFE169", "yellow_soft": "#FFF4C9",
}


def theme_name() -> str:
    """MargeAI has one look: light, yellow and blue."""
    return "light"


def palette() -> dict[str, str]:
    return LIGHT if theme_name() == "light" else DARK


def accents() -> tuple[str, str, str, str, str]:
    """(green, amber, red, grey, blue) for the active theme, for charts and table cell colours."""
    p = palette()
    return p["green"], p["amber"], p["red"], p["muted"], p["blue"]


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _variables(p: dict[str, str]) -> str:
    out = {f"--mg-{k.replace('_', '-')}": v for k, v in p.items()}
    for name in ("green", "amber", "red", "blue", "muted"):
        out[f"--mg-{name}-bg"] = _rgba(p[name], 0.12)
    return ":root {" + "; ".join(f"{k}: {v}" for k, v in out.items()) + "}"


_RULES = """
  .block-container { padding-top: 2rem; max-width: 1400px; }
  html, body, [class*="st-"] { line-height: 1.5; }
  h2 { margin-bottom: 0.2rem; }
  h3 { margin-top: 1.8rem; padding-bottom: 4px; border-bottom: 3px solid var(--mg-yellow); display: inline-block; }
  h4 { color: var(--mg-text2); }
  section[data-testid="stSidebar"] h3 { font-size: 1rem; margin-top: 0.8rem; }
  button[kind="primary"] { font-weight: 700; }
  div[data-testid="stMetric"] {
    background: var(--mg-surface); border: 1px solid var(--mg-border); border-radius: 14px; padding: 14px 16px;
    box-shadow: 0 2px 6px rgba(20, 40, 75, 0.08);
  }
  div[data-testid="stMetricLabel"] p { color: var(--mg-muted); text-transform: uppercase;
    font-size: 0.68rem; letter-spacing: 0.05em; }
  div[data-testid="stMetricValue"] { font-size: 1.7rem; }
  div[data-testid="stMetricDelta"] { font-size: 0.75rem; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.75rem;
    font-weight: 600; letter-spacing: 0.04em; }
  .badge-live { background: var(--mg-green-bg); color: var(--mg-green); border: 1px solid var(--mg-green); }
  .badge-sample { background: var(--mg-amber-bg); color: var(--mg-amber); border: 1px solid var(--mg-amber); }
  .badge-idle { background: var(--mg-muted-bg); color: var(--mg-muted); border: 1px solid var(--mg-muted); }
  .badge-sim { background: var(--mg-blue-bg); color: var(--mg-blue); border: 1px solid var(--mg-blue); }
  .subtle { color: var(--mg-muted); font-size: 0.85rem; }
  .card { background: var(--mg-surface); border: 1px solid var(--mg-border); border-radius: 10px;
    padding: 14px 16px; height: 100%; }
  .card h4 { margin: 0 0 6px 0; font-size: 0.95rem; }
  .pin { position: absolute; transform: translate(-50%, -50%); background: var(--mg-blue);
    color: var(--mg-on-accent); border-radius: 50%; width: 26px; height: 26px; display: flex;
    align-items: center; justify-content: center; font-weight: 700; font-size: 0.8rem;
    border: 2px solid #ffffff; }
  .hero { margin-bottom: 0.6rem; }
  .hero .lede { color: var(--mg-text2); font-size: 1.02rem; max-width: 60rem; margin-top: 2px; }
  .hero .hint { color: var(--mg-muted); font-size: 0.85rem; margin-top: 6px; }
  .flow { display: flex; gap: 8px; margin: 0.6rem 0 1.2rem; flex-wrap: wrap; }
  .flow .step { flex: 1 1 180px; border: 1px solid var(--mg-border); border-radius: 10px; padding: 8px 12px;
    background: var(--mg-surface); color: var(--mg-muted); font-size: 0.82rem; }
  .flow .step b { color: var(--mg-text2); font-size: 0.86rem; }
  .flow .step .n { display: inline-block; width: 20px; height: 20px; border-radius: 50%; text-align: center;
    line-height: 20px; font-size: 0.72rem; font-weight: 700; margin-right: 6px;
    background: var(--mg-border); color: var(--mg-text2); }
  .flow .step.active { border-color: var(--mg-blue); background: var(--mg-blue-bg); color: var(--mg-text2); }
  .flow .step.active b { color: var(--mg-text); }
  .flow .step.active .n, .flow .step.done .n { background: var(--mg-blue); color: var(--mg-on-accent); }
  .callout { border-radius: 10px; padding: 10px 14px; margin: 0.4rem 0 0.8rem; font-size: 0.9rem;
    border: 1px solid var(--mg-border); background: var(--mg-surface); color: var(--mg-text); }
  .callout b.ttl { display: block; margin-bottom: 2px; }
  .callout.action { border-color: var(--mg-amber); background: var(--mg-amber-bg); }
  .callout.good { border-color: var(--mg-green); background: var(--mg-green-bg); }
  .callout.bad { border-color: var(--mg-red); background: var(--mg-red-bg); }
  .brandbar { display: flex; align-items: center; gap: 18px; padding: 14px 20px; border-radius: 20px;
    background: linear-gradient(135deg, var(--mg-yellow-soft) 0%, var(--mg-yellow) 100%);
    border: 1px solid var(--mg-border); box-shadow: 0 3px 10px rgba(20, 40, 75, 0.10); margin-bottom: 0.9rem; }
  .brandbar svg { flex: none; filter: drop-shadow(0 2px 3px rgba(20, 40, 75, 0.25)); }
  .brandbar .wordmark { font-size: 2.3rem; font-weight: 800; letter-spacing: -0.03em; line-height: 1.05;
    color: var(--mg-text); }
  .brandbar .wordmark span { color: var(--mg-blue); }
  .brandbar .tagline { color: var(--mg-text2); font-size: 1rem; margin-top: 2px; }
  div[data-testid="stExpander"] { border-radius: 12px; }
  button[kind="primary"] { border-radius: 10px; box-shadow: 0 2px 6px rgba(31, 95, 191, 0.30); }
"""


def inject() -> None:
    p = palette()
    st.markdown(f"<style>{_variables(p)}{_RULES}</style>", unsafe_allow_html=True)


JOURNEY = [
    ("Find improvements", "See where your product wins and loses, then pick changes"),
    ("Compare supplier quotes", "Three simulated vendors quote your changes"),
    ("Approve the order", "You review and approve a purchase order"),
]


def flow_bar(active: int) -> None:
    """Three-step 'where am I' bar for the product-to-order flow. active is 1, 2 or 3."""
    cells = []
    for i, (title, hint) in enumerate(JOURNEY, 1):
        cls = "active" if i == active else "done" if i < active else ""
        mark = "✓" if i < active else str(i)
        cells.append(
            f'<div class="step {cls}"><span class="n">{mark}</span><b>{title}</b>'
            f"<div>{hint}</div></div>"
        )
    st.markdown(f'<div class="flow">{"".join(cells)}</div>', unsafe_allow_html=True)


def hero(title: str, lede: str, hint: str = "", badge_html: str = "") -> None:
    """Page header: a title, one plain sentence on what the page does, and an optional hint."""
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"## {title}")
    with right:
        if badge_html:
            st.markdown(f"<div style='text-align:right; margin-top:1.3rem'>{badge_html}</div>", unsafe_allow_html=True)
    hint_html = f'<div class="hint">{hint}</div>' if hint else ""
    st.markdown(f'<div class="hero"><div class="lede">{lede}</div>{hint_html}</div>', unsafe_allow_html=True)


LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 120" width="{w}" height="{h}" role="img" '
    'aria-label="MargeAI logo">'
    '<rect x="2" y="2" width="96" height="116" rx="26" fill="#FFE169"/>'
    '<g stroke="#173F86" stroke-width="2.6" stroke-linejoin="round" fill="#2F6FDB">'
    '<path d="M24 94 C12 96 7 82 14 72 C16 82 21 85 29 84 Z"/>'
    '<path d="M76 94 C88 96 93 82 86 72 C84 82 79 85 71 84 Z"/>'
    '<path d="M22 98 C15 80 16 56 21 38 C26 18 37 7 50 7 C63 7 74 18 79 38 C84 56 85 80 78 98 '
    'C70 105 30 105 22 98 Z"/></g>'
    '<g fill="none" stroke="#9CC4FF" stroke-width="2.6" stroke-linecap="round">'
    '<path d="M50 10 C41 22 37 38 39 56"/><path d="M50 10 C59 22 63 38 61 56"/>'
    '<path d="M43 11 C33 23 29 40 30 58"/><path d="M57 11 C67 23 71 40 70 58"/></g>'
    '<path d="M31 36 C33 26 39 19 45 15" fill="none" stroke="#DCEBFF" stroke-width="3.2" stroke-linecap="round"/>'
    '<text x="50" y="79" text-anchor="middle" font-family="Arial Black, Arial, Helvetica, sans-serif" '
    'font-weight="900" font-size="13" fill="#FFFFFF" letter-spacing="0.5">MARGE</text>'
    '<text x="50" y="94" text-anchor="middle" font-family="Arial Black, Arial, Helvetica, sans-serif" '
    'font-weight="900" font-size="15" fill="#FFE169">AI</text></svg>'
)


def logo_svg(height: int = 64) -> str:
    """The MargeAI logo (an original tall blue beehive hairdo with the name inside, on a yellow badge)."""
    return LOGO_SVG.format(w=round(height * 100 / 120), h=height)


def brand_header(lede: str, hint: str = "") -> None:
    """The MargeAI banner: logo, wordmark and tagline, then one plain sentence on what the page does."""
    st.markdown(
        f'<div class="brandbar">{logo_svg(96)}<div><div class="wordmark">Marge<span>AI</span></div>'
        f'<div class="tagline">The AI co-pilot that minds your margin.</div></div></div>',
        unsafe_allow_html=True,
    )
    hint_html = f'<div class="hint">{hint}</div>' if hint else ""
    st.markdown(f'<div class="hero"><div class="lede">{lede}</div>{hint_html}</div>', unsafe_allow_html=True)


def callout(text: str, title: str = "", kind: str = "") -> None:
    """A highlighted note. kind: '' (neutral), 'action' (needs you), 'good' or 'bad'."""
    head = f'<b class="ttl">{title}</b>' if title else ""
    st.markdown(f'<div class="callout {kind}">{head}{text}</div>', unsafe_allow_html=True)


GLOSSARY = {
    "Margin": "What you keep from each sale after every cost, as a share of the selling price.",
    "Duty / tariff": "A tax charged when the product is imported. A higher rate raises your cost per unit.",
    "FBA fee": "What Amazon charges to store, pack and ship an order for you.",
    "Referral fee": "Amazon's cut of each sale (about 15% for most clothing).",
    "SKU": "One specific product version you sell, for example one style in one size and colour.",
    "RFQ": "Request for quote: you ask a supplier what they would charge for a given order.",
    "PO": "Purchase order: the document that formally orders units from a supplier.",
    "Landed cost": "The true cost per unit once it arrives: product, shipping and duty.",
}


def glossary() -> None:
    """Plain-English definitions, collapsed in the sidebar."""
    with st.sidebar.expander("What do these terms mean?"):
        for term, meaning in GLOSSARY.items():
            st.markdown(f"**{term}**: {meaning}")


def next_link(page: str, label: str, icon: str = "➡️") -> None:
    """A button to the next page. If the link cannot be made, say where to click instead."""
    try:
        st.page_link(page, label=label, icon=icon)
    except Exception:
        st.info(f"Open **{label}** in the sidebar menu.")
