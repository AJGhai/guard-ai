"""MarginGuard dashboard: drives the MarginGuard ZooWork agent through api/server.py.

Start the API first, then the dashboard (from the repo root):
    .venv/bin/uvicorn api.server:app --port 8000
    .venv/bin/streamlit run dashboard/app.py

The dashboard holds no keys and runs no pricing logic. It starts a run (POST /run), polls
/events, /pending, /plan, /report and /outbox, and sends approval decisions (POST /approve).
Set MARGINGUARD_API to use an API somewhere other than http://127.0.0.1:8000.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import altair as alt
import httpx
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.pricing_rules import REVIEW_MARGIN  # noqa: E402  (threshold line only)

API = os.environ.get("MARGINGUARD_API", "http://127.0.0.1:8000").rstrip("/")
POLL_SECONDS = 1.5

GREEN, AMBER, RED, GREY = "#00d395", "#f5a623", "#ff5c6c", "#6b7785"
ACTION_COLORS = {"REPRICE": GREEN, "APPLIED": GREEN, "REJECTED": AMBER, "ABSORB": GREY, "REVIEW": RED}
STATUS_ICONS = {
    "running": "⏳ running",
    "done": "✅ done",
    "awaiting approval": "✋ awaiting approval",
    "approved": "✅ approved",
    "rejected": "⛔ rejected",
    "blocked": "⛔ blocked",
    "error": "❌ error",
}
TOOL_LABELS = {"get_comparables": "get_comparables · ZooData"}

st.set_page_config(page_title="MarginGuard", page_icon=":shield:", layout="wide")
st.markdown(
    f"""
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
      .badge-idle {{ background: rgba(107,119,133,.15); color: {GREY}; border: 1px solid {GREY}; }}
      .subtle {{ color: {GREY}; font-size: 0.85rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- API ----------

def api(method: str, path: str, **kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Call the MarginGuard API. Returns (json, error code or message)."""
    try:
        resp = httpx.request(method, f"{API}{path}", timeout=10, **kwargs)
    except httpx.HTTPError as exc:
        return None, f"MarginGuard API unreachable at {API} ({type(exc).__name__})"
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.is_error:
        return data, data.get("error") or f"HTTP {resp.status_code}"
    return data, None


# ---------- formatting ----------

def pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.1%}"


def money(x: float | None) -> str:
    return "–" if x is None else f"${x:,.0f}"


def pts(a: float | None, b: float | None, label: str) -> str | None:
    return None if a is None or b is None else f"{(a - b) * 100:+.1f} pts {label}"


# ---------- activity ----------

def activity_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per tool call, with its latest status, built from the run's event trace."""
    rows: list[dict[str, Any]] = []

    def find(agent: str, tool: str, statuses: tuple[str, ...]) -> dict[str, Any] | None:
        return next(
            (r for r in rows if r["agent"] == agent and r["tool"] == tool and r["status"] in statuses),
            None,
        )

    for e in events:
        label, detail = e["label"], e["detail"]
        agent = "SupplierNegotiator" if label.startswith("negotiator.") else "MarginGuard"
        kind = label.removeprefix("negotiator.")
        if kind == "tool.start":
            tool, _, args = detail.partition(" ")
            rows.append({"t": e["t"], "agent": agent, "tool": tool, "status": "running", "detail": args})
        elif kind == "local.result":
            tool, _, summary = detail.partition(": ")
            row = find(agent, tool, ("running", "awaiting approval", "approved"))
            if row:
                row["status"] = "error" if summary.startswith("error") else "done"
                row["detail"] = summary
        elif kind == "approval.requested":
            row = find(agent, detail.split(" ")[0], ("running",))
            if row:
                row["status"] = "awaiting approval"
        elif kind == "approval.resolved":
            row = find(agent, detail.split(" ")[0], ("awaiting approval",)) if detail.split(" ")[0] else None
            row = row or next((r for r in reversed(rows) if r["agent"] == agent and r["status"] == "awaiting approval"), None)
            if row:
                row["status"] = "rejected" if "deny" in detail else "approved"
        elif kind == "tool.blocked":
            row = find(agent, detail.split(" ")[0], ("running", "awaiting approval"))
            if row:
                row["status"] = "rejected" if "approval-denied" in detail else "blocked"
                row["detail"] = detail
    return rows


# ---------- sidebar: scenario and run ----------

with st.sidebar:
    st.markdown("### Tariff scenario")
    duty_old = st.slider("Current duty", 0, 100, 12, format="%d%%")
    duty_new = st.slider("New duty", 0, 100, 37, format="%d%%")
    request = f"Duty goes from {duty_old}% to {duty_new}% on all products. What should we do?"
    st.caption(f"Request to MarginGuard: “{request}”")

    _, status_err = api("GET", "/pending")
    if st.button("Run MarginGuard", type="primary", width="stretch"):
        _, err = api("POST", "/run", json={"request": request, "reset": True})
        if err == "run_in_progress":
            st.session_state["flash"] = ("warning", "A run is already in progress.")
        elif err:
            st.session_state["flash"] = ("error", f"Could not start the run: {err}")
        else:
            st.session_state["flash"] = ("success", "Run started. Store prices reset and outbox cleared.")
    st.caption("Each run resets store prices to the originals and clears the supplier outbox.")
    st.markdown("### Connection")
    st.caption(f"API: {API}")
    if status_err:
        st.error(status_err)


# ---------- live view ----------

@st.fragment(run_every=POLL_SECONDS)
def live() -> None:
    events_data, events_err = api("GET", "/events")
    if events_err == "no_run":
        header(None, None)
        st.info("No run yet. Pick a duty scenario and press **Run MarginGuard**.")
        return
    if events_err:
        st.error(events_err)
        return

    pending, _ = api("GET", "/pending")
    plan, _ = api("GET", "/plan")
    report, report_err = api("GET", "/report")
    outbox, _ = api("GET", "/outbox")
    if report_err:
        report = None  # 409 report_not_ready until the run ends
    plan = plan or {}
    status = events_data["status"]
    session = events_data.get("session_id") or "run"

    header(status, (report or {}).get("data_source") or plan.get("data_source"))
    if flash := st.session_state.pop("flash", None):
        getattr(st, flash[0])(flash[1])
    if status == "failed":
        st.error(f"Run failed: {events_data.get('error')}")

    kpis(plan)

    left, right = st.columns([3, 2], gap="large")
    with left:
        approvals(pending or {}, session)
        decisions_table(plan)
        margin_chart(plan)
    with right:
        agent_activity(events_data["events"])
        supplier_outbox(outbox or {})
    if report:
        with st.expander("Agent report", expanded=status == "succeeded"):
            st.markdown(report.get("report") or "")


def header(status: str | None, source: str | None) -> None:
    left, right = st.columns([3, 2])
    with left:
        st.markdown("## MarginGuard")
        st.markdown(
            '<span class="subtle">Tariff-aware pricing for fashion merchants, run by the MarginGuard '
            "ZooWork agent. A person approves every price change and supplier email.</span>",
            unsafe_allow_html=True,
        )
    with right:
        if source == "live_zoodata":
            badge = '<span class="badge badge-live">LIVE ZOODATA</span>'
        elif source:
            badge = '<span class="badge badge-sample">SAMPLE</span> <span class="subtle">market/fallback_prices.json</span>'
        else:
            badge = '<span class="badge badge-idle">NO DATA YET</span>'
        state = f'<br><span class="subtle">Run status: {status.replace("_", " ")}</span>' if status else ""
        st.markdown(f"<div style='text-align:right; margin-top:1.4rem'>{badge}{state}</div>", unsafe_allow_html=True)


def kpis(plan: dict[str, Any]) -> None:
    baseline = plan.get("baseline_totals") or {}
    proposed = plan.get("scenario_totals") or {}
    applied = plan.get("applied_totals")
    today = plan.get("blended_margin_today")
    tariff = baseline.get("blended_margin")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Duty exposure / mo", money(baseline.get("exposure")))
    k2.metric("Margin today", pct(today))
    k3.metric("Margin, tariff", pct(tariff), pts(tariff, today, "vs today"))
    if applied:
        k4.metric("Margin, approved", pct(applied["blended_margin"]), pts(applied["blended_margin"], tariff, "vs tariff"))
        k5.metric("Recovery / mo", money(applied["recovery"]), f"plan {money(proposed.get('recovery'))}", delta_color="off")
    else:
        k4.metric("Margin, proposed", pct(proposed.get("blended_margin")), pts(proposed.get("blended_margin"), tariff, "vs tariff"))
        k5.metric("Recovery / mo (proposed)", money(proposed.get("recovery")), "awaiting approval" if proposed else None, delta_color="off")


def approvals(pending: dict[str, Any], session: str) -> None:
    st.markdown("### Approvals")
    if not pending.get("pending"):
        st.caption("Nothing waiting. Price changes and supplier emails appear here when the agent asks.")
        return
    kind = pending.get("kind")
    items = pending.get("changes") if kind == "price_changes" else pending.get("emails")
    keys = {item["sku"]: f"ok_{session}_{kind}_{item['sku']}" for item in items or []}
    if kind == "price_changes":
        st.caption("The agent is waiting. Untick a change to reject it, then submit.")
        for c in items:
            c1, c2 = st.columns([3, 4])
            c1.toggle(f"{c['sku']}  ${c['old_price']:g} → ${c['new_price']:g}", value=True, key=keys[c["sku"]])
            c2.markdown(f'<span class="subtle">{c["reason"]}</span>', unsafe_allow_html=True)
    else:
        st.caption("Supplier email drafts. Approved drafts go to the outbox; nothing is sent.")
        for e in items:
            with st.container(border=True):
                st.toggle(f"{e['sku']} → {e['supplier']} <{e['to']}>", value=True, key=keys[e["sku"]])
                st.markdown(f"**{e['subject']}**")
                st.text(e["body"])
    decisions = {sku: bool(st.session_state.get(key, True)) for sku, key in keys.items()}
    approved = sum(decisions.values())
    label = "price change(s)" if kind == "price_changes" else "email(s)"
    if st.button(f"Submit: approve {approved}, reject {len(decisions) - approved} {label}", type="primary"):
        result, err = api("POST", "/approve", json={"decisions": decisions})
        if err:
            st.error(f"Approval failed: {err}")
        else:
            st.success(f"Approved {result['approved'] or 'none'}; rejected {result['rejected'] or 'none'}.")


def decisions_table(plan: dict[str, Any]) -> None:
    st.markdown("### Pricing decisions")
    decisions = plan.get("decisions")
    if not decisions:
        st.caption("Decisions appear once the agent has evaluated the pricing rules.")
        return
    skus = plan.get("skus") or {}
    applied = {r["sku"] for r in plan.get("applied") or []}
    rejected = {r["sku"] for r in plan.get("rejected") or []}
    rows = []
    for d in decisions:
        m = skus.get(d["sku"], {})
        action = d["action"]
        if action == "REPRICE" and d["sku"] in applied:
            action = "APPLIED"
        elif action == "REPRICE" and d["sku"] in rejected:
            action = "REJECTED"
        rows.append({
            "SKU": d["sku"],
            "Product": d["name"],
            "Action": action,
            "Current $": d["current_price"],
            "Proposed $": d["new_price"],
            "Market median $": d["median"],
            "Margin today": m.get("margin_today"),
            "Margin under tariff": d["tariff_margin"],
            "Margin after": m.get("margin_after", m.get("margin_plan")),
            "Why": d["reason"],
        })
    styled = (
        pd.DataFrame(rows)
        .style.map(lambda v: f"color: {ACTION_COLORS.get(v, GREY)}; font-weight: 700", subset=["Action"])
        .format({
            "Current $": "{:,.0f}",
            "Proposed $": "{:,.0f}",
            "Market median $": "{:,.2f}",
            "Margin today": "{:.1%}",
            "Margin under tariff": "{:.1%}",
            "Margin after": "{:.1%}",
        }, na_rep="–")
    )
    st.dataframe(styled, width="stretch", hide_index=True, height=392)
    if not plan.get("applied_totals"):
        st.caption("“Margin after” shows the proposed plan until the price changes are approved.")


def margin_chart(plan: dict[str, Any]) -> None:
    skus = plan.get("skus")
    if not skus:
        return
    st.markdown("### Margin by product")
    after_label = "After approved" if plan.get("applied_totals") else "Proposed plan"
    chart_df = pd.DataFrame([
        {"SKU": sku, "Stage": stage, "Margin": value}
        for sku, m in skus.items()
        for stage, value in (
            ("Today", m["margin_today"]),
            ("Under tariff", m["margin_tariff"]),
            (after_label, m.get("margin_after", m["margin_plan"])),
        )
    ])
    bars = alt.Chart(chart_df).mark_bar().encode(
        x=alt.X("SKU:N", title=None, sort=None),
        xOffset=alt.XOffset("Stage:N", sort=["Today", "Under tariff", after_label]),
        y=alt.Y("Margin:Q", axis=alt.Axis(format="%"), title=None),
        color=alt.Color(
            "Stage:N",
            scale=alt.Scale(domain=["Today", "Under tariff", after_label], range=[GREY, RED, GREEN]),
            legend=alt.Legend(orient="top", title=None),
        ),
        tooltip=["SKU", "Stage", alt.Tooltip("Margin:Q", format=".1%")],
    )
    threshold = alt.Chart(pd.DataFrame({"y": [REVIEW_MARGIN]})).mark_rule(
        color=AMBER, strokeDash=[4, 4]
    ).encode(y="y:Q")
    st.altair_chart((bars + threshold).properties(height=320), width="stretch")
    st.caption(f"Dashed line: {REVIEW_MARGIN:.0%} sourcing-review threshold.")


def agent_activity(events: list[dict[str, Any]]) -> None:
    st.markdown("### Agent activity")
    rows = activity_rows(events)
    if not rows:
        st.caption("Waiting for the agent's first tool call…")
    else:
        st.table(
            pd.DataFrame([
                {
                    "t (s)": f"{r['t']:.1f}",
                    "Status": STATUS_ICONS.get(r["status"], r["status"]),
                    "Tool": TOOL_LABELS.get(r["tool"], r["tool"]),
                    "Agent": "Negotiator" if r["agent"] == "SupplierNegotiator" else r["agent"],
                }
                for r in rows
            ]).set_index("t (s)")
        )
    st.caption("Tool arguments and results are in the event log.")
    with st.expander("Event log"):
        st.dataframe(pd.DataFrame(events[-40:]), width="stretch", hide_index=True)


def supplier_outbox(outbox: dict[str, Any]) -> None:
    st.markdown("### Supplier outbox")
    emails = outbox.get("emails") or []
    if not emails:
        st.caption("Approved supplier emails land here. Nothing is ever sent.")
        return
    st.caption("Approved drafts in data/outbox/. Not sent.")
    for e in emails:
        with st.container(border=True):
            st.markdown(f"**{e['sku']}** · `{e['path']}`")
            st.text(e["content"])


live()
