"""MarginGuard: tariff-aware pricing agent on ZooWork Managed Agents.

Custom tools (all executed locally by this script):
  - load_cost_sheet       no approval   rows of data/sample_merchant_data.csv
  - calc_margins          no approval   tools/calc_margins.py
  - get_comparables       no approval   tools/comparables.py (market/fallback_prices.json for now)
  - evaluate_pricing_rules no approval  tools/pricing_rules.py (REVIEW / REPRICE / ABSORB per SKU)
  - get_store_prices      no approval   data/store_prices.json
  - apply_price_changes   approval      asks y/n per change; writes only approved changes

Current prices live in data/store_prices.json (single source of truth).

Usage:
  .venv/bin/python agent/run.py "Duty goes from 12% to 37% on all products. What should we do?"
  .venv/bin/python agent/run.py --reset "..."   # restore data/store_prices.original.json first
  .venv/bin/python agent/run.py                 # prompts for the message
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from zoowork import (  # noqa: E402
    SessionEvent,
    ZooworkClient,
    assistant_text,
    create_zoowork_client,
    custom_tool_use,
    is_run_finished,
    run_outcome,
    tool_call,
)

from tools.calc_margins import calc_margins, load_cost_sheet  # noqa: E402
from tools.comparables import get_comparables  # noqa: E402
from tools.pricing_rules import evaluate_pricing_rules  # noqa: E402

PRICES_FILE = ROOT / "data" / "store_prices.json"
ORIGINAL_PRICES_FILE = ROOT / "data" / "store_prices.original.json"
AGENT_ID_FILE = Path(__file__).resolve().parent / ".agent_id"
CONFIG_HASH_FILE = Path(__file__).resolve().parent / ".agent_config_hash"

load_dotenv(ROOT / ".env")

INSTRUCTIONS = """\
# MarginGuard

You are MarginGuard, a pricing analyst for a fashion brand facing a tariff change.
Current prices come from the store (`get_store_prices`); the cost sheet already uses them.

## Decision rules
Apply these per SKU, in this order, using the tariff-scenario margin from `calc_margins`
(`margin_new` at current prices):
a) REVIEW if tariff margin < 56%. Do not reprice it; flag it for sourcing review.
b) Otherwise REPRICE if the current price is below the comparable median.
   New price = min(median, round(current_price * 1.10)) in whole dollars.
   Only reprice if the increase is at least $2; otherwise ABSORB.
c) Otherwise ABSORB.

## Workflow
1. Call `load_cost_sheet` for every SKU's category, unit cost, current price and monthly units.
2. Call `calc_margins` with `prices: {}` and `volume_drop: 0` for baseline (`margin_old`) and
   tariff-scenario (`margin_new`) margins and total `exposure`. Duty rates are decimals (12% = 0.12).
3. Call `get_comparables` once per category. Queries: Pants -> "women linen pants",
   Dresses -> "cotton midi dress", Jackets -> "women denim jacket".
4. Call `evaluate_pricing_rules` with the duty rates and `medians` keyed by cost-sheet category
   (Pants, Dresses, Jackets). It applies the decision rules above exactly; use its actions,
   new prices and reasons as given.
5. Recalculate the plan with `calc_margins`, passing `reprice_prices` from step 4 and
   `volume_drop: 0.02`.
6. If there are REPRICE items, always call `apply_price_changes` once with all of them. Do not
   stop to ask the user whether to submit them: the tool itself asks a human to approve or
   reject each change, and only approved changes are written. Pass
   `scenario` with the same duty rates and volume_drop 0.02. Use each SKU's `reason` from
   `evaluate_pricing_rules` word for word; it cites the competitor median and says when the
   +10% cap limited the price.
7. After the result returns, write a short report:
   - A per-SKU decision table: SKU, action, old price, new price, reason. For REPRICE items
     rejected by the reviewer, show the action as REJECTED and the new price as the old price.
   - Sections: Applied, Rejected, Absorbed, Review (sourcing review), Exposure,
     Estimated recovery, Assumptions.
   - Estimated recovery is `applied_totals.recovery` from the `apply_price_changes` result,
     which covers only the approved changes.
   - Assumptions must state the comparables data source from the `data_source` field of the
     `get_comparables` results: "live_zoodata" (live ZooData) or "sample_fallback" (sample
     fallback data, not live market prices).

## Rules
- Never do arithmetic yourself. Every margin, price, exposure, recovery or total you report must
  come from a tool result.
- Do not retry a rejected price change.
- Finish the whole workflow in one turn, including `apply_price_changes`; never end the turn
  with a question about whether to submit the changes.
- Amounts from `calc_margins` are per month.
"""

NO_ARGS = {"type": "object", "properties": {}}

CUSTOM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "load_cost_sheet",
        "description": (
            "Return the merchant cost sheet: one row per SKU with sku, name, category, origin, "
            "unit_cost, price (current store price) and units_per_month."
        ),
        "input_schema": NO_ARGS,
    },
    {
        "name": "calc_margins",
        "description": (
            "Calculate landed costs, margins and repricing impact. Input: duty_old and duty_new "
            "as decimals, prices as {sku: proposed_price} (omitted SKUs keep their current "
            "price; pass {} for the baseline), volume_drop as a decimal unit loss on repriced "
            "SKUs. Returns per-SKU landed_old, landed_new, margin_old, margin_new, impact, and "
            "totals exposure, recovery, blended_margin (all per month)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duty_old": {"type": "number"},
                "duty_new": {"type": "number"},
                "prices": {"type": "object", "additionalProperties": {"type": "number"}},
                "volume_drop": {"type": "number"},
            },
            "required": ["duty_old", "duty_new", "prices", "volume_drop"],
        },
    },
    {
        "name": "get_comparables",
        "description": (
            "Return competitor listings for a product category: category, n, low, high, "
            "median, listings with title, price, change_90d (fraction) and rating, and "
            "data_source (live_zoodata or sample_fallback)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"category_query": {"type": "string"}},
            "required": ["category_query"],
        },
    },
    {
        "name": "evaluate_pricing_rules",
        "description": (
            "Apply the MarginGuard decision rules (REVIEW / REPRICE / ABSORB) to every SKU at "
            "current store prices. Input: duty_old and duty_new as decimals, medians as "
            "{cost-sheet category: comparable median}. Returns decisions (sku, action, "
            "current_price, new_price, median, tariff_margin, reason) and reprice_prices "
            "{sku: new_price} for the REPRICE items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duty_old": {"type": "number"},
                "duty_new": {"type": "number"},
                "medians": {"type": "object", "additionalProperties": {"type": "number"}},
            },
            "required": ["duty_old", "duty_new", "medians"],
        },
    },
    {
        "name": "get_store_prices",
        "description": "Return the live store prices as a JSON object mapping SKU to price.",
        "input_schema": NO_ARGS,
    },
    {
        "name": "apply_price_changes",
        "description": (
            "Request price changes for one or more SKUs. A human reviews each change; only "
            "approved changes are written to the store. The result lists applied and rejected "
            "changes and, when scenario is given, applied_totals (calc_margins totals for the "
            "approved changes only). Do not retry rejected changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "new_price": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["sku", "new_price", "reason"],
                    },
                },
                "scenario": {
                    "type": "object",
                    "properties": {
                        "duty_old": {"type": "number"},
                        "duty_new": {"type": "number"},
                        "volume_drop": {"type": "number"},
                    },
                    "required": ["duty_old", "duty_new", "volume_drop"],
                },
            },
            "required": ["changes"],
        },
    },
]

AGENT_CONFIG: dict[str, Any] = {
    "custom_tools": CUSTOM_TOOLS,
    "tool_policy": {
        "permissions": {
            "load_cost_sheet": "always_allow",
            "calc_margins": "always_allow",
            "get_comparables": "always_allow",
            "evaluate_pricing_rules": "always_allow",
            "get_store_prices": "always_allow",
            "apply_price_changes": "always_ask",
        }
    },
    "persona": {"docs": [{"name": "AGENTS.md", "content": INSTRUCTIONS}]},
}


# ---------- helpers ----------

def fmt(price: Any) -> str:
    return f"{price:g}" if isinstance(price, (int, float)) else str(price)


def short(value: Any, limit: int = 140) -> str:
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 3] + "..."


def read_prices() -> dict[str, float]:
    return json.loads(PRICES_FILE.read_text())


def write_prices(prices: dict[str, float]) -> None:
    PRICES_FILE.write_text(json.dumps(prices, indent=2) + "\n")


def reset_prices() -> None:
    shutil.copyfile(ORIGINAL_PRICES_FILE, PRICES_FILE)


def parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw if isinstance(raw, dict) else {}


def parse_changes(raw: Any) -> list[dict[str, Any]]:
    """Normalize tool arguments (dict or JSON string) into a list of changes."""
    changes = parse_args(raw).get("changes") or []
    return [c for c in changes if isinstance(c, dict)]


def describe_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Changes as shown to a reviewer: sku, old_price, new_price, reason."""
    prices = read_prices()
    return [
        {
            "sku": str(c.get("sku")),
            "old_price": prices.get(str(c.get("sku"))),
            "new_price": c.get("new_price"),
            "reason": c.get("reason", ""),
        }
        for c in changes
    ]


# An approver gets the described changes and returns {sku: approved}.
Approver = Callable[[list[dict[str, Any]]], Awaitable[dict[str, bool]]]


async def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please answer y or n.")


async def terminal_approver(changes: list[dict[str, Any]]) -> dict[str, bool]:
    """Ask y/n in the terminal for each change."""
    decisions: dict[str, bool] = {}
    print("\n=== Approval required: apply_price_changes ===")
    for c in changes:
        print(f"  {c['sku']}: {fmt(c['old_price'])} -> {fmt(c['new_price'])}   reason: {c['reason']}")
        decisions[c["sku"]] = await ask_yes_no(f"  Approve {c['sku']} -> {fmt(c['new_price'])}? [y/n] ")
    print()
    return decisions


def apply_price_changes(
    changes: list[dict[str, Any]],
    decisions: dict[str, bool],
    scenario: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write only approved changes to data/store_prices.json."""
    prices = read_prices()
    applied, rejected = [], []
    for change in changes:
        sku, new_price = str(change.get("sku")), float(change.get("new_price"))
        if not decisions.get(sku, False):
            rejected.append({"sku": sku, "new_price": new_price, "status": "rejected by reviewer"})
        elif sku not in prices:
            rejected.append({"sku": sku, "new_price": new_price, "status": "unknown SKU"})
        elif new_price <= 0:
            rejected.append({"sku": sku, "new_price": new_price, "status": "invalid price"})
        else:
            old = prices[sku]
            prices[sku] = int(new_price) if new_price.is_integer() else new_price
            applied.append({"sku": sku, "old_price": old, "new_price": prices[sku]})
    result: dict[str, Any] = {"applied": applied, "rejected": rejected}
    if scenario:
        # Measure recovery for the approved changes against the prices before this write.
        result["applied_totals"] = calc_margins(
            {**scenario, "prices": {row["sku"]: row["new_price"] for row in applied}}
        )["totals"]
    if applied:
        write_prices(prices)
    result["prices"] = prices
    return result


def summarize_result(name: str, result: dict[str, Any]) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    if name == "load_cost_sheet":
        return f"{len(result['rows'])} rows"
    if name == "calc_margins":
        return f"totals {short(result['totals'])}"
    if name == "get_comparables":
        return (
            f"{result['category']}: n={result['n']} low={result['low']} median={result['median']} "
            f"high={result['high']} source={result['data_source']}"
        )
    if name == "evaluate_pricing_rules":
        counts: dict[str, int] = {}
        for d in result["decisions"]:
            counts[d["action"]] = counts.get(d["action"], 0) + 1
        return f"{counts} reprice={short(result['reprice_prices'])}"
    if name == "apply_price_changes":
        extra = f", applied_totals {short(result['applied_totals'])}" if "applied_totals" in result else ""
        return f"applied {len(result['applied'])}, rejected {len(result['rejected'])}{extra}"
    return short(result)


# ---------- agent lifecycle ----------

def config_fingerprint() -> str:
    return hashlib.sha256(json.dumps(AGENT_CONFIG, sort_keys=True).encode()).hexdigest()


async def get_or_create_agent(client: ZooworkClient) -> tuple[str, str]:
    """Return (agent_id, what happened: existing / config_updated / created)."""
    fingerprint = config_fingerprint()

    if AGENT_ID_FILE.exists():
        agent_id = AGENT_ID_FILE.read_text().strip()
        stored = CONFIG_HASH_FILE.read_text().strip() if CONFIG_HASH_FILE.exists() else ""
        if stored == fingerprint:
            return agent_id, "existing"
        # Push the tools, approval policy and instructions only when they changed.
        await client.update_agent(agent_id, AGENT_CONFIG)
        CONFIG_HASH_FILE.write_text(fingerprint + "\n")
        return agent_id, "config_updated"

    models = await client.list_models()
    selectable = [m for m in models if m.get("selectable") is not False]
    if not selectable:
        raise RuntimeError("No selectable ZooWork model")
    chosen = next(
        (m for m in selectable if "model" in (m.get("default_for") or [])), selectable[0]
    )
    resource = {"name": "marginguard", "model": {"primary": chosen["model"]}, **AGENT_CONFIG}
    created = await client.create_agent(resource, idempotency_key="marginguard-agent-v1")
    agent_id = created["agent_id"]
    AGENT_ID_FILE.write_text(agent_id + "\n")
    CONFIG_HASH_FILE.write_text(fingerprint + "\n")
    return agent_id, "created"


# ---------- one run ----------

class MarginGuardRun:
    """One request to the agent: event trace, plan, approvals and final report.

    Used by the terminal (echo=True, terminal_approver) and by api/server.py.
    status: idle -> running <-> awaiting_approval -> succeeded | failed
    """

    def __init__(self, approver: Approver, echo: bool = False) -> None:
        self.approver = approver
        self.echo = echo
        self.status = "idle"
        self.error: str | None = None
        self.session_id: str | None = None
        self.events: list[dict[str, Any]] = []
        self.plan: dict[str, Any] = {"comparables": {}}
        self.apply_result: dict[str, Any] | None = None
        self.report_parts: list[str] = []
        self._start = time.monotonic()

    @property
    def report(self) -> str:
        return "\n\n".join(self.report_parts)

    def trace(self, label: str, detail: str = "") -> None:
        elapsed = round(time.monotonic() - self._start, 1)
        self.events.append({"seq": len(self.events), "t": elapsed, "label": label, "detail": detail})
        if self.echo:
            print(f"[{elapsed:6.1f}s] {label:<22} {detail}".rstrip(), flush=True)

    async def review(self, raw_changes: list[dict[str, Any]]) -> dict[str, bool]:
        self.status = "awaiting_approval"
        try:
            return await self.approver(describe_changes(raw_changes))
        finally:
            self.status = "running"

    # -- tools --

    async def execute_tool(
        self, name: str | None, raw_args: Any, approved: dict[str, bool] | None
    ) -> dict[str, Any]:
        try:
            args = parse_args(raw_args)
            if name == "load_cost_sheet":
                return {"rows": load_cost_sheet()}
            if name == "calc_margins":
                result = calc_margins(args)
                key = "scenario" if args.get("prices") else "baseline"
                self.plan[key] = {"input": args, "totals": result["totals"], "skus": result["skus"]}
                return result
            if name == "get_comparables":
                result = get_comparables(args)
                if "error" not in result:
                    self.plan["comparables"][result["category"]] = {
                        k: result[k] for k in ("n", "low", "median", "high", "data_source")
                    }
                return result
            if name == "evaluate_pricing_rules":
                result = evaluate_pricing_rules(args)
                self.plan["decisions"] = result["decisions"]
                self.plan["reprice_prices"] = result["reprice_prices"]
                return result
            if name == "get_store_prices":
                return read_prices()
            if name == "apply_price_changes":
                changes = parse_changes(args)
                if approved is None:
                    # No approval event seen for this call: review before writing anyway.
                    approved = await self.review(changes)
                result = apply_price_changes(changes, approved, args.get("scenario"))
                for row in result["applied"]:
                    self.trace("price.written", f"{row['sku']}: {fmt(row['old_price'])} -> {fmt(row['new_price'])}")
                for row in result["rejected"]:
                    self.trace("price.skipped", f"{row['sku']} -> {fmt(row['new_price'])} ({row['status']})")
                self.apply_result = result
                return result
            return {"error": f"unknown tool {name}"}
        except Exception as exc:  # report tool failures back to the model
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- events --

    def trace_event(self, event: SessionEvent) -> None:
        p = event.payload
        kind = event.event_type
        if kind == "run.started":
            self.trace("run.started", event.run_id or "")
        elif kind == "agent.tool":
            tc = tool_call(event)
            if tc is None:
                return
            if tc.phase == "start":
                self.trace("tool.start", f"{tc.tool_name} {short(tc.args or {})}")
            elif tc.phase == "end":
                self.trace("tool.end", f"{tc.tool_name}{' (error)' if tc.is_error else ''}")
            else:
                self.trace("tool.blocked", f"{tc.tool_name} reason={p.get('deniedReason')}")
        elif kind == "agent.custom_tool_use":
            call = custom_tool_use(event)
            if call is None:
                return
            if call.phase == "requested":
                self.trace("custom_tool.requested", f"{call.name} {short(call.input or {})}")
            else:
                self.trace("custom_tool.resolved", f"{call.name or call.call_id} outcome={call.outcome}")
        elif kind == "agent.approval":
            if p.get("phase") == "requested":
                self.trace("approval.requested", f"{p.get('toolName')} approval={p.get('approvalId')}")
            else:
                self.trace("approval.resolved", f"{p.get('toolName') or ''} resolution={p.get('resolution')}")
        elif kind == "agent.error":
            self.trace("agent.error", short(p.get("errorMessage") or p))
        elif kind == "agent.assistant":
            text = assistant_text(event).strip()
            if text:
                self.report_parts.append(text)
                self.trace("assistant", short(text, 80))
                if self.echo:
                    print("\n" + text + "\n", flush=True)
        elif kind == "run.finished":
            self.trace("run.finished", f"outcome={run_outcome(event)}")

    async def _turn(self, client: ZooworkClient, agent_id: str, message: str) -> None:
        session = await client.create_session(
            agent_id, {"initial_events": [{"type": "user.message", "content": message}]}
        )
        self.session_id = session["session_id"]
        self.trace("session.created", self.session_id)
        self.trace("user.message", message)

        # Reviewer decisions per tool call, keyed by toolCallId.
        decisions_by_call: dict[str, dict[str, bool]] = {}

        async for event in client.stream_events(agent_id, self.session_id):
            self.trace_event(event)
            p = event.payload

            if event.event_type == "agent.approval" and p.get("phase") == "requested":
                if p.get("toolName") != "apply_price_changes":
                    # Nothing else is configured to ask; deny anything unexpected.
                    await client.resolve_approval(
                        agent_id, p["approvalId"], decision="deny", resolved_by="marginguard"
                    )
                    continue
                decisions = await self.review(parse_changes(p.get("arguments")))
                decisions_by_call[p.get("toolCallId") or ""] = decisions
                decision = "allow-once" if any(decisions.values()) else "deny"
                await client.resolve_approval(
                    agent_id, p["approvalId"], decision=decision, resolved_by="marginguard-reviewer"
                )

            call = custom_tool_use(event)
            if call is not None and call.phase == "requested":
                approved = decisions_by_call.get(call.tool_call_id or "")
                result = await self.execute_tool(call.name, call.input or {}, approved)
                self.trace("local.result", f"{call.name}: {summarize_result(call.name or '', result)}")
                await client.resolve_custom_tool_call(
                    agent_id,
                    call.call_id,
                    content=[{"type": "json", "value": result}],
                    is_error="error" in result,
                    resolved_by="marginguard",
                )

            if is_run_finished(event):
                outcome = run_outcome(event)
                if outcome != "succeeded":
                    raise RuntimeError(f"Run ended with outcome: {outcome}")
                break

    async def run(self, message: str, reset: bool = False) -> None:
        self.status = "running"
        try:
            if reset:
                reset_prices()
                self.trace("prices.reset", f"{ORIGINAL_PRICES_FILE.name} -> {PRICES_FILE.name}")
            async with create_zoowork_client() as client:
                agent_id, action = await get_or_create_agent(client)
                if action != "existing":
                    self.trace(f"agent.{action}", agent_id)
                await client.start_agent(agent_id)
                await client.wait_until_running(agent_id, timeout=120)
                await self._turn(client, agent_id, message)
            self.status = "succeeded"
        except Exception as exc:
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            self.trace("run.failed", self.error)
            raise


# ---------- terminal mode ----------

async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MarginGuard pricing agent.")
    parser.add_argument(
        "--reset", action="store_true",
        help="copy data/store_prices.original.json over data/store_prices.json first",
    )
    parser.add_argument("message", nargs="*", help="request for the agent")
    opts = parser.parse_args()

    message = " ".join(opts.message).strip() or input("Message for the agent: ").strip()
    await MarginGuardRun(terminal_approver, echo=True).run(message, reset=opts.reset)


if __name__ == "__main__":
    asyncio.run(main())
