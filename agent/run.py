"""MarginGuard: tariff-aware pricing agent on ZooWork Managed Agents.

Custom tools (all executed locally by this script):
  - load_cost_sheet       no approval   rows of data/sample_merchant_data.csv
  - calc_margins          no approval   tools/calc_margins.py
  - get_comparables       no approval   tools/comparables.py (live ZooData, cached; sample fallback)
  - evaluate_pricing_rules no approval  tools/pricing_rules.py (REVIEW / REPRICE / ABSORB per SKU)
  - get_store_prices      no approval   data/store_prices.json
  - apply_price_changes   approval      asks y/n per change; writes only approved changes
  - request_supplier_negotiation  no approval  runs a SupplierNegotiator session (below)

SupplierNegotiator (second agent, started by request_supplier_negotiation for REVIEW SKUs):
  - calc_target_cost      no approval   tools/supplier.py (unit cost that restores a 60% margin)
  - get_comparables       no approval   tools/comparables.py
  - send_supplier_email   approval      asks y/n per draft; writes approved drafts to
                                        data/outbox/<sku>.md. No email is ever sent.

Current prices live in data/store_prices.json (single source of truth).

Usage:
  .venv/bin/python agent/run.py "Duty goes from 12% to 37% on all products. What should we do?"
  .venv/bin/python agent/run.py --reset "..."   # restore original prices, clear the outbox
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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
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
from tools.supplier import (  # noqa: E402
    calc_target_cost,
    clear_outbox,
    describe_emails,
    send_supplier_emails,
)

PRICES_FILE = ROOT / "data" / "store_prices.json"
ORIGINAL_PRICES_FILE = ROOT / "data" / "store_prices.original.json"
AGENT_DIR = Path(__file__).resolve().parent
AGENT_ID_FILE = AGENT_DIR / ".agent_id"
CONFIG_HASH_FILE = AGENT_DIR / ".agent_config_hash"

# The server can drop an idle event stream (for example while a nested negotiator session or a
# human approval keeps us busy); reconnect this many times in a row before giving up.
STREAM_RECONNECTS = 5

# Comparables query per cost-sheet category (also in the MarginGuard instructions).
CATEGORY_QUERIES = {
    "Pants": "women linen pants",
    "Dresses": "cotton midi dress",
    "Jackets": "women denim jacket",
}

# Approval kinds passed to an approver along with the items to decide.
PRICE_CHANGES = "price_changes"
SUPPLIER_EMAILS = "supplier_emails"

load_dotenv(ROOT / ".env")

INSTRUCTIONS = """\
# MarginGuard

You are MarginGuard, a pricing analyst for a fashion brand facing a tariff change.
Current prices come from the store; `evaluate_pricing_rules` already uses them.

## Decision rules
Apply these per SKU, in this order, using the tariff-scenario margin at current prices:
a) REVIEW if tariff margin < 56%. Do not reprice it; flag it for sourcing review.
b) Otherwise REPRICE if the current price is below the comparable median.
   New price = min(median, round(current_price * 1.10)) in whole dollars.
   Only reprice if the increase is at least $2; otherwise ABSORB.
c) Otherwise ABSORB.

## Workflow
1. In ONE parallel batch, call `get_comparables` for all three categories: Pants ->
   "women linen pants", Dresses -> "cotton midi dress", Jackets -> "women denim jacket".
2. Call `evaluate_pricing_rules` with the duty rates (decimals; 12% = 0.12), `medians` keyed by
   cost-sheet category (Pants, Dresses, Jackets) and `volume_drop: 0.02`. It applies the
   decision rules above exactly and also returns `baseline_totals` (with `exposure`) and
   `plan_totals` (with the full-plan `recovery`), so do not call `calc_margins` or
   `load_cost_sheet`. Use its actions, new prices and reasons as given.
3. If there are REPRICE items, call `apply_price_changes` once with all of them. Do not stop to
   ask the user first: the tool asks a human to approve or reject each change, and only approved
   changes are written. Pass `scenario` with the same duty rates and volume_drop 0.02. Use each
   SKU's `reason` from `evaluate_pricing_rules` word for word.
4. If any SKU is REVIEW, after `apply_price_changes` has returned (or right after step 2 when
   there are no REPRICE items), call `request_supplier_negotiation` once with all REVIEW SKUs and
   the same duty rates. A human approves or rejects each supplier email draft; approved drafts go
   to the outbox (no email is sent).
5. Write a concise report:
   - A per-SKU decision table: SKU, action, old price, new price, reason. For REPRICE items
     rejected by the reviewer, show the action as REJECTED and the new price as the old price.
   - Sections, one or two lines each: Applied, Rejected, Absorbed, Review (sourcing review),
     Supplier negotiation, Exposure, Estimated recovery, Assumptions.
   - Supplier negotiation: per REVIEW SKU, the supplier, current -> target unit cost and
     reduction_pct from `request_supplier_negotiation`, and whether the email was approved
     (written to the outbox, not sent) or rejected by the reviewer.
   - Exposure is `baseline_totals.exposure`. Estimated recovery is `applied_totals.recovery`
     from `apply_price_changes` (approved changes only); also give the full-plan
     `plan_totals.recovery`.
   - Assumptions must state the comparables data source from the `data_source` field of the
     `get_comparables` results: "live_zoodata" (live ZooData) or "sample_fallback" (sample
     fallback data, not live market prices).

## Rules
- Never do arithmetic yourself. Every margin, price, exposure, recovery or total you report must
  come from a tool result.
- Do not retry a rejected price change.
- Call `apply_price_changes` only after `evaluate_pricing_rules` has returned, and never call
  `request_supplier_negotiation` in parallel with `apply_price_changes`.
- Finish the whole workflow in one turn, including `apply_price_changes` and
  `request_supplier_negotiation`; never end the turn with a question about whether to submit.
- Amounts are per month.
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
            "current_price, new_price, median, tariff_margin, reason), reprice_prices "
            "{sku: new_price} for the REPRICE items, baseline_totals (calc_margins totals at "
            "current prices, including exposure) and plan_totals (calc_margins totals with all "
            "reprice_prices and volume_drop, including the full-plan recovery). All per month."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duty_old": {"type": "number"},
                "duty_new": {"type": "number"},
                "medians": {"type": "object", "additionalProperties": {"type": "number"}},
                "volume_drop": {"type": "number", "default": 0.02},
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

CUSTOM_TOOLS.append({
    "name": "request_supplier_negotiation",
    "description": (
        "Hand REVIEW SKUs to the SupplierNegotiator agent. It calculates the unit cost that "
        "restores a 60% margin, drafts a cost-reduction email to each SKU's supplier, and asks a "
        "human to approve each draft. Approved drafts are written to the outbox; no email is "
        "sent. Returns targets (supplier, current_cost, target_cost, reduction_pct per SKU), "
        "written and rejected drafts, and the negotiator's summary."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "skus": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "duty_old": {"type": "number"},
            "duty_new": {"type": "number"},
        },
        "required": ["skus", "duty_old", "duty_new"],
    },
})

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
            "request_supplier_negotiation": "always_allow",
        }
    },
    "persona": {"docs": [{"name": "AGENTS.md", "content": INSTRUCTIONS}]},
}


NEGOTIATOR_INSTRUCTIONS = """\
# SupplierNegotiator

You write for the sourcing team of Guard Apparel. A duty increase has raised our landed cost on
some SKUs and the market will not bear a higher retail price, so you ask each SKU's supplier to
share the tariff burden with a lower unit cost.

## Workflow
1. In ONE parallel batch, call `calc_target_cost` for every SKU (with the duty rates from the
   request, as decimals) and `get_comparables` for every query named in the request.
2. Draft one email per SKU to its `supplier`, then call `send_supplier_email` once with all
   drafts. A human approves or rejects each draft; approved drafts go to the outbox and nothing
   is actually sent. Do not ask before calling it, and do not retry rejected drafts.
3. Reply with exactly one line per SKU and nothing else: supplier, `current_cost` ->
   `target_cost`, `reduction_phrase`, and whether the draft was approved (in the outbox, not
   sent) or rejected.

## Email rules
- Under 120 words including the sign-off, with a clear subject line naming the SKU.
- State the duty increase from the request (for example "from 12% to 37%") and what it adds per
  unit: `duty_increase_per_unit` (for example "$2.80 per unit"). Do not name a country or market.
- Ask for a unit cost of `target_cost` (down from `current_cost`) as a way to share the tariff
  burden, and describe the change exactly as `reduction_phrase` (for example "an 11.4%
  reduction").
- Cite the competitor `median` from `get_comparables` next to our current `price` to show we
  cannot raise the retail price.
- State our volume as "We buy <units_per_month> units per month".
- Offer a 12-month volume commitment in return.
- Never mention our margin, a target margin, profit, or how the target cost was worked out.
- Sign off exactly: "Sourcing Team, Guard Apparel".

## Rules
- Never do arithmetic yourself. Quote every number as a tool returned it; write money with two
  decimals (for example $2.80).
- Do not invent facts that no tool returned (lead times, order history, competitor names).
- Finish in one turn.
"""

NEGOTIATOR_TOOLS: list[dict[str, Any]] = [
    {
        "name": "calc_target_cost",
        "description": (
            "Target unit cost for a SKU after the duty change (calculated internally; never "
            "mention how). Returns sku, name, category, price, units_per_month, supplier, "
            "contact_email, current_cost, target_cost, reduction_per_unit, reduction_pct "
            "(negative = lower cost), reduction_phrase (for example 'an 11.4% reduction') and "
            "duty_increase_per_unit (extra duty in dollars per unit)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "target_margin": {"type": "number", "default": 0.60},
                "duty_old": {"type": "number"},
                "duty_new": {"type": "number"},
            },
            "required": ["sku", "duty_new"],
        },
    },
    next(t for t in CUSTOM_TOOLS if t["name"] == "get_comparables"),
    {
        "name": "send_supplier_email",
        "description": (
            "Submit email drafts to suppliers. A human approves or rejects each draft; approved "
            "drafts are written to the outbox (data/outbox/<sku>.md) and no email is actually "
            "sent. The recipient is the SKU's supplier on file. Returns written and rejected "
            "drafts. Do not retry rejected drafts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "emails": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "subject": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["sku", "subject", "body"],
                    },
                },
            },
            "required": ["emails"],
        },
    },
]

# A fast model with low thinking: the negotiator only fills in tool numbers and writes two short
# emails. MarginGuard keeps the platform default model chosen when it was created.
NEGOTIATOR_MODEL = {"primary": "litellm/claude-haiku-5-5", "thinking": "low"}

NEGOTIATOR_CONFIG: dict[str, Any] = {
    "model": NEGOTIATOR_MODEL,
    "custom_tools": NEGOTIATOR_TOOLS,
    "tool_policy": {
        "permissions": {
            "calc_target_cost": "always_allow",
            "get_comparables": "always_allow",
            "send_supplier_email": "always_ask",
        }
    },
    "persona": {"docs": [{"name": "AGENTS.md", "content": NEGOTIATOR_INSTRUCTIONS}]},
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


def parse_emails(raw: Any) -> list[dict[str, Any]]:
    """Normalize send_supplier_email arguments into a list of drafts."""
    emails = parse_args(raw).get("emails") or []
    return [e for e in emails if isinstance(e, dict)]


# An approver gets a kind (PRICE_CHANGES or SUPPLIER_EMAILS) and the described items, and
# returns {sku: approved}.
Approver = Callable[[str, list[dict[str, Any]]], Awaitable[dict[str, bool]]]


async def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please answer y or n.")


async def terminal_approver(kind: str, items: list[dict[str, Any]]) -> dict[str, bool]:
    """Ask y/n in the terminal for each price change or supplier email."""
    decisions: dict[str, bool] = {}
    if kind == SUPPLIER_EMAILS:
        print("\n=== Approval required: send_supplier_email (outbox only; nothing is sent) ===")
        for e in items:
            print(f"\n  --- {e['sku']} ---")
            print(f"  To: {e['supplier']} <{e['to']}>")
            print(f"  Subject: {e['subject']}\n")
            print("\n".join("  " + line for line in e["body"].strip().splitlines()))
            print(f"\n  ({len(e['body'].split())} words)")
            decisions[e["sku"]] = await ask_yes_no(
                f"\n  Approve email to {e['supplier']} for {e['sku']}? [y/n] "
            )
        print()
        return decisions
    print("\n=== Approval required: apply_price_changes ===")
    for c in items:
        print(f"  {c['sku']}: {fmt(c['old_price'])} -> {fmt(c['new_price'])}   reason: {c['reason']}")
        decisions[c["sku"]] = await ask_yes_no(f"  Approve {c['sku']} -> {fmt(c['new_price'])}? [y/n] ")
    print()
    return decisions


def apply_price_changes(
    changes: list[dict[str, Any]],
    decisions: dict[str, bool],
    scenario: dict[str, Any] | None = None,
    base_prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Write only approved changes to data/store_prices.json.

    applied_totals are measured against base_prices (the run's starting snapshot) when given.
    """
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
        # Measure recovery for the approved changes against the run's starting prices.
        result["applied_totals"] = calc_margins(
            {**scenario, "prices": {row["sku"]: row["new_price"] for row in applied}},
            base_prices=base_prices,
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
        return (
            f"{counts} reprice={short(result['reprice_prices'])} "
            f"exposure={result['baseline_totals']['exposure']} plan_recovery={result['plan_totals']['recovery']}"
        )
    if name == "apply_price_changes":
        extra = f", applied_totals {short(result['applied_totals'])}" if "applied_totals" in result else ""
        return f"applied {len(result['applied'])}, rejected {len(result['rejected'])}{extra}"
    if name == "calc_target_cost":
        return (
            f"{result['sku']}: cost {fmt(result['current_cost'])} -> {fmt(result['target_cost'])} "
            f"({fmt(result['reduction_pct'])}%), duty +{fmt(result['duty_increase_per_unit'])}/unit, "
            f"{result['units_per_month']} units/month"
        )
    if name in ("send_supplier_email", "request_supplier_negotiation"):
        written = [w["sku"] for w in result.get("written", [])]
        rejected = [r["sku"] for r in result.get("rejected", [])]
        return f"outbox {written}, rejected {rejected} (no email sent)"
    return short(result)


# ---------- agent lifecycle ----------

@dataclass(frozen=True)
class AgentSpec:
    name: str
    config: dict[str, Any]
    id_file: Path
    hash_file: Path


MARGINGUARD = AgentSpec("marginguard", AGENT_CONFIG, AGENT_ID_FILE, CONFIG_HASH_FILE)
NEGOTIATOR = AgentSpec(
    "supplier-negotiator", NEGOTIATOR_CONFIG,
    AGENT_DIR / ".negotiator_agent_id", AGENT_DIR / ".negotiator_config_hash",
)


def config_fingerprint(config: dict[str, Any] = AGENT_CONFIG) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


async def get_or_create_agent(
    client: ZooworkClient, spec: AgentSpec = MARGINGUARD
) -> tuple[str, str]:
    """Return (agent_id, what happened: existing / config_updated / created)."""
    fingerprint = config_fingerprint(spec.config)

    if spec.id_file.exists():
        agent_id = spec.id_file.read_text().strip()
        stored = spec.hash_file.read_text().strip() if spec.hash_file.exists() else ""
        if stored == fingerprint:
            return agent_id, "existing"
        # Push the tools, approval policy and instructions only when they changed.
        await client.update_agent(agent_id, spec.config)
        spec.hash_file.write_text(fingerprint + "\n")
        return agent_id, "config_updated"

    resource = {"name": spec.name, **spec.config}
    if "model" not in spec.config:
        models = await client.list_models()
        selectable = [m for m in models if m.get("selectable") is not False]
        if not selectable:
            raise RuntimeError("No selectable ZooWork model")
        chosen = next(
            (m for m in selectable if "model" in (m.get("default_for") or [])), selectable[0]
        )
        resource["model"] = {"primary": chosen["model"]}
    created = await client.create_agent(resource, idempotency_key=f"{spec.name}-agent-v1")
    agent_id = created["agent_id"]
    spec.id_file.write_text(agent_id + "\n")
    spec.hash_file.write_text(fingerprint + "\n")
    return agent_id, "created"


# ---------- sessions ----------

class AgentSession:
    """One session on one agent: event trace, custom tools and one approval-gated tool.

    Subclasses set APPROVAL_TOOL / APPROVAL_KIND and implement review_items and execute_tool.
    A child session (parent given) writes its trace and status to the parent run.
    """

    APPROVAL_TOOL = ""
    APPROVAL_KIND = ""
    TRACE_PREFIX = ""
    AFTER_REVIEW_PHASE = ""

    def __init__(
        self, approver: Approver, echo: bool = False, parent: AgentSession | None = None
    ) -> None:
        self.approver = approver
        self.echo = echo
        self.parent = parent
        self.status = "idle"
        self.error: str | None = None
        self.session_id: str | None = None
        self.events: list[dict[str, Any]] = []
        self.report_parts: list[str] = []
        self.reviewed: dict[str, bool] = {}
        self._start = time.monotonic()
        self.phases: list[dict[str, Any]] = []
        self._phase: tuple[str, float] | None = None

    @property
    def report(self) -> str:
        return "\n\n".join(self.report_parts)

    def trace(self, label: str, detail: str = "") -> None:
        if self.parent is not None:
            self.parent.trace(self.TRACE_PREFIX + label, detail)
            return
        elapsed = round(time.monotonic() - self._start, 1)
        self.events.append({"seq": len(self.events), "t": elapsed, "label": label, "detail": detail})
        if self.echo:
            print(f"[{elapsed:6.1f}s] {label:<22} {detail}".rstrip(), flush=True)

    def set_status(self, status: str) -> None:
        (self.parent or self).status = status

    def phase(self, name: str | None) -> None:
        """Close the current timing phase and start `name` (None just closes it).

        Phases named "approval: ..." are human time and are excluded from agent time.
        """
        if self.parent is not None:
            self.parent.phase(name)
            return
        now = time.monotonic()
        if self._phase is not None:
            label, started = self._phase
            self.phases.append({
                "phase": label,
                "seconds": round(now - started, 1),
                "human": label.startswith("approval:"),
            })
        self._phase = (name, now) if name else None

    def timings(self) -> dict[str, Any]:
        agent = sum(p["seconds"] for p in self.phases if not p["human"])
        human = sum(p["seconds"] for p in self.phases if p["human"])
        return {"phases": self.phases, "agent_seconds": round(agent, 1), "human_seconds": round(human, 1)}

    def print_timings(self) -> None:
        t = self.timings()
        print("\n=== Timings ===")
        for p in t["phases"]:
            print(f"  {p['phase']:<44} {p['seconds']:6.1f}s")
        print(f"  {'agent time (excluding approvals)':<44} {t['agent_seconds']:6.1f}s")
        print(f"  {'approval time (human)':<44} {t['human_seconds']:6.1f}s", flush=True)

    def review_items(self, raw_args: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def execute_tool(
        self, name: str | None, args: dict[str, Any], approved: dict[str, bool] | None
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def review(self, raw_args: Any) -> dict[str, bool]:
        self.set_status("awaiting_approval")
        self.phase(f"approval: {self.APPROVAL_KIND} (human)")
        try:
            decisions = await self.approver(self.APPROVAL_KIND, self.review_items(raw_args))
        finally:
            self.set_status("running")
            self.phase(self.AFTER_REVIEW_PHASE)
        self.reviewed.update(decisions)
        return decisions

    async def ready_agent(self, client: ZooworkClient, spec: AgentSpec) -> str:
        agent_id, action = await get_or_create_agent(client, spec)
        if action != "existing":
            self.trace(f"agent.{action}", f"{spec.name} {agent_id}")
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)
        return agent_id

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

    async def _events(self, client: ZooworkClient, agent_id: str) -> AsyncIterator[SessionEvent]:
        """Session events, reconnecting after a dropped stream.

        Resumes from the cursor of the last event the caller finished handling, and skips any
        event already yielded, so a reconnect never re-runs a tool or re-asks for approval.
        """
        assert self.session_id is not None
        cursor: str | None = None
        last_seq = -1
        failures = 0
        while True:
            try:
                async for event in client.stream_events(agent_id, self.session_id, cursor=cursor):
                    if event.seq <= last_seq:
                        continue
                    failures = 0
                    yield event
                    # The caller has finished with this event; resume after it if we reconnect.
                    last_seq = event.seq
                    cursor = event.cursor or cursor
                reason = "stream closed before the run finished"
            except httpx.TransportError as exc:
                reason = f"{type(exc).__name__}: {exc}"
            failures += 1
            if failures > STREAM_RECONNECTS:
                raise RuntimeError(f"Event stream lost after {STREAM_RECONNECTS} reconnects: {reason}")
            self.trace("stream.reconnect", f"{short(reason, 80)} (attempt {failures})")
            await asyncio.sleep(min(failures, 3))

    async def _turn(self, client: ZooworkClient, agent_id: str, message: str) -> None:
        session = await client.create_session(
            agent_id, {"initial_events": [{"type": "user.message", "content": message}]}
        )
        self.session_id = session["session_id"]
        self.trace("session.created", self.session_id)
        self.trace("user.message", message)

        # Reviewer decisions per tool call, keyed by toolCallId.
        decisions_by_call: dict[str, dict[str, bool]] = {}

        async for event in self._events(client, agent_id):
            self.trace_event(event)
            p = event.payload

            if event.event_type == "agent.approval" and p.get("phase") == "requested":
                if p.get("toolName") != self.APPROVAL_TOOL:
                    # Nothing else is configured to ask; deny anything unexpected.
                    await client.resolve_approval(
                        agent_id, p["approvalId"], decision="deny", resolved_by="marginguard"
                    )
                    continue
                decisions = await self.review(p.get("arguments"))
                decisions_by_call[p.get("toolCallId") or ""] = decisions
                decision = "allow-once" if any(decisions.values()) else "deny"
                await client.resolve_approval(
                    agent_id, p["approvalId"], decision=decision, resolved_by="marginguard-reviewer"
                )

            call = custom_tool_use(event)
            if call is not None and call.phase == "requested":
                approved = decisions_by_call.get(call.tool_call_id or "")
                try:
                    result = await self.execute_tool(call.name, parse_args(call.input or {}), approved)
                except Exception as exc:  # report tool failures back to the model
                    result = {"error": f"{type(exc).__name__}: {exc}"}
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


class NegotiatorRun(AgentSession):
    """A SupplierNegotiator session started from MarginGuard's request_supplier_negotiation."""

    APPROVAL_TOOL = "send_supplier_email"
    APPROVAL_KIND = SUPPLIER_EMAILS
    TRACE_PREFIX = "negotiator."
    AFTER_REVIEW_PHASE = "negotiator: write outbox + summary"

    def __init__(self, parent: MarginGuardRun) -> None:
        super().__init__(parent.approver, parent.echo, parent)
        self.base_prices = parent.base_prices
        self.targets: dict[str, dict[str, Any]] = {}
        self.send_result: dict[str, Any] | None = None

    def review_items(self, raw_args: Any) -> list[dict[str, Any]]:
        return describe_emails(parse_emails(raw_args))

    async def execute_tool(
        self, name: str | None, args: dict[str, Any], approved: dict[str, bool] | None
    ) -> dict[str, Any]:
        if name == "calc_target_cost":
            result = calc_target_cost(args, base_prices=self.base_prices)
            self.targets[result["sku"]] = result
            return result
        if name == "get_comparables":
            return get_comparables(args)
        if name == "send_supplier_email":
            emails = parse_emails(args)
            if approved is None:
                # No approval event seen for this call: review before writing anyway.
                approved = await self.review(args)
            result = send_supplier_emails(emails, approved)
            for row in result["written"]:
                self.trace("email.outbox", f"{row['sku']} -> {row['to']} ({row['path']}, not sent)")
            for row in result["rejected"]:
                self.trace("email.skipped", f"{row['sku']} ({row['status']})")
            self.send_result = result
            return result
        return {"error": f"unknown tool {name}"}

    async def negotiate(
        self,
        agent_id: str,
        client: ZooworkClient,
        skus: list[str],
        duty_old: float,
        duty_new: float,
        categories: dict[str, str],
    ) -> dict[str, Any]:
        self.phase("negotiator: targets + drafts")
        lines = []
        for sku in skus:
            category = categories.get(sku)
            query = CATEGORY_QUERIES.get(category or "")
            lines.append(f"- {sku} ({category}): get_comparables query \"{query}\"" if query else f"- {sku}")
        message = (
            f"Negotiate unit cost reductions for these REVIEW SKUs. Duty goes from "
            f"{duty_old * 100:g}% to {duty_new * 100:g}% (duty_old {duty_old}, duty_new {duty_new}).\n"
            + "\n".join(lines)
        )
        await self._turn(client, agent_id, message)
        if self.send_result is not None:
            written, rejected = self.send_result["written"], self.send_result["rejected"]
        else:
            # The reviewer rejected every draft, so send_supplier_email never ran.
            written = []
            rejected = [{"sku": s, "status": "rejected by reviewer"} for s, ok in self.reviewed.items() if not ok]
        return {
            "skus": skus,
            "targets": [
                {k: t[k] for k in ("sku", "supplier", "contact_email", "units_per_month",
                                   "current_cost", "target_cost", "reduction_pct")}
                for t in self.targets.values()
            ],
            "written": written,
            "rejected": rejected,
            "emails_sent": False,
            "negotiator_summary": self.report,
        }


class MarginGuardRun(AgentSession):
    """One request to MarginGuard: event trace, plan, approvals and final report.

    Used by the terminal (echo=True, terminal_approver) and by api/server.py.
    status: idle -> running <-> awaiting_approval -> succeeded | failed
    """

    APPROVAL_TOOL = "apply_price_changes"
    APPROVAL_KIND = PRICE_CHANGES
    AFTER_REVIEW_PHASE = "marginguard: apply + hand-off"

    def __init__(self, approver: Approver, echo: bool = False) -> None:
        super().__init__(approver, echo)
        self.plan: dict[str, Any] = {"comparables": {}}
        self.base_prices: dict[str, float] | None = None
        self.apply_result: dict[str, Any] | None = None
        self.negotiation: dict[str, Any] | None = None
        self.client: ZooworkClient | None = None
        self._negotiator_agent: asyncio.Task[str] | None = None

    def review_items(self, raw_args: Any) -> list[dict[str, Any]]:
        return describe_changes(parse_changes(raw_args))

    async def execute_tool(
        self, name: str | None, args: dict[str, Any], approved: dict[str, bool] | None
    ) -> dict[str, Any]:
        if name == "load_cost_sheet":
            return {"rows": load_cost_sheet(base_prices=self.base_prices)}
        if name == "calc_margins":
            result = calc_margins(args, base_prices=self.base_prices)
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
            result = evaluate_pricing_rules(args, base_prices=self.base_prices)
            self.plan["decisions"] = result["decisions"]
            self.plan["reprice_prices"] = result["reprice_prices"]
            duties = {k: result["plan_input"][k] for k in ("duty_old", "duty_new")}
            self.plan["baseline"] = {
                "input": {**duties, "prices": {}, "volume_drop": 0},
                "totals": result["baseline_totals"],
            }
            self.plan["scenario"] = {"input": result["plan_input"], "totals": result["plan_totals"]}
            self.plan["skus"] = self._sku_margins(duties, result["plan_input"])
            return result
        if name == "get_store_prices":
            return read_prices()
        if name == "apply_price_changes":
            changes = parse_changes(args)
            if approved is None:
                # No approval event seen for this call: review before writing anyway.
                approved = await self.review(args)
            result = apply_price_changes(
                changes, approved, args.get("scenario"), base_prices=self.base_prices
            )
            for row in result["applied"]:
                self.trace("price.written", f"{row['sku']}: {fmt(row['old_price'])} -> {fmt(row['new_price'])}")
            for row in result["rejected"]:
                self.trace("price.skipped", f"{row['sku']} -> {fmt(row['new_price'])} ({row['status']})")
            self.apply_result = result
            if args.get("scenario"):
                after = calc_margins(
                    {**args["scenario"], "prices": {r["sku"]: r["new_price"] for r in result["applied"]}},
                    base_prices=self.base_prices,
                )["skus"]
                for sku, row in (self.plan.get("skus") or {}).items():
                    row["margin_after"] = after[sku]["margin_new"]
            return result
        if name == "request_supplier_negotiation":
            skus = [str(s) for s in args.get("skus") or []]
            if not skus:
                return {"error": "skus must list at least one REVIEW SKU"}
            review = {d["sku"] for d in self.plan.get("decisions") or [] if d["action"] == "REVIEW"}
            if review and not set(skus) <= review:
                return {"error": f"only REVIEW SKUs can be negotiated: {sorted(review)}"}
            assert self.client is not None and self._negotiator_agent is not None
            categories = {d["sku"]: d["category"] for d in self.plan.get("decisions") or []}
            result = await NegotiatorRun(self).negotiate(
                await self._negotiator_agent, self.client, skus,
                float(args["duty_old"]), float(args["duty_new"]), categories,
            )
            self.phase("marginguard: final report")
            self.negotiation = result
            return result
        return {"error": f"unknown tool {name}"}

    def _sku_margins(self, duties: dict[str, float], plan_input: dict[str, Any]) -> dict[str, Any]:
        """Per-SKU margins for the dashboard (not sent to the agent): today (old duty), under the
        tariff at current prices, and with the proposed plan. margin_after is added on approval."""
        baseline = calc_margins({**duties, "prices": {}, "volume_drop": 0}, base_prices=self.base_prices)
        plan = calc_margins(plan_input, base_prices=self.base_prices)
        # Blended margin today: the same calculation with the new duty equal to the old one.
        today = calc_margins(
            {"duty_old": duties["duty_old"], "duty_new": duties["duty_old"], "prices": {}, "volume_drop": 0},
            base_prices=self.base_prices,
        )
        self.plan["blended_margin_today"] = today["totals"]["blended_margin"]
        return {
            sku: {
                "margin_today": row["margin_old"],
                "margin_tariff": row["margin_new"],
                "margin_plan": plan["skus"][sku]["margin_new"],
            }
            for sku, row in baseline["skus"].items()
        }

    async def run(self, message: str, reset: bool = False) -> None:
        self.status = "running"
        try:
            if reset:
                reset_prices()
                clear_outbox()
                self.trace("prices.reset", f"{ORIGINAL_PRICES_FILE.name} -> {PRICES_FILE.name}; outbox cleared")
            # Baseline for every margin in this run, so approved writes can't shift plan numbers.
            self.base_prices = read_prices()
            self.trace("prices.snapshot", short(self.base_prices))
            async with create_zoowork_client() as client:
                self.client = client
                self.phase("setup: start agents")
                # Start the negotiator alongside MarginGuard so it is ready when needed.
                self._negotiator_agent = asyncio.create_task(self.ready_agent(client, NEGOTIATOR))
                agent_id = await self.ready_agent(client, MARGINGUARD)
                self.phase("marginguard: analysis")
                await self._turn(client, agent_id, message)
                self.phase(None)
            self.status = "succeeded"
            if self.echo:
                self.print_timings()
        except Exception as exc:
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            self.trace("run.failed", self.error)
            raise
        finally:
            self.phase(None)
            if self._negotiator_agent is not None and not self._negotiator_agent.done():
                self._negotiator_agent.cancel()
            self.client = None


# ---------- terminal mode ----------

async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MarginGuard pricing agent.")
    parser.add_argument(
        "--reset", action="store_true",
        help="copy data/store_prices.original.json over data/store_prices.json and clear the outbox first",
    )
    parser.add_argument("message", nargs="*", help="request for the agent")
    opts = parser.parse_args()

    message = " ".join(opts.message).strip() or input("Message for the agent: ").strip()
    await MarginGuardRun(terminal_approver, echo=True).run(message, reset=opts.reset)


if __name__ == "__main__":
    asyncio.run(main())
