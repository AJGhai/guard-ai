"""Product advisor: a ZooWork Managed Agent that turns buyer complaints into product changes.

advise(product, complaints, economics) -> {"summary": str, "improvements": [...], "source": ...}

The agent proposes changes and rough cost and price effects. It does not do the margin math;
the dashboard recomputes margin from tools/unit_economics.py using the numbers it returns.
Needs ZOOWORK_API_KEY. If anything fails, a labelled sample answer is returned so the demo
keeps working (source="sample").

State files (ignored by git): agent/.advisor_agent_id and agent/.advisor_config_hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from zoowork import assistant_text, create_zoowork_client, is_run_finished, run_outcome

HERE = Path(__file__).resolve().parent
AGENT_ID_FILE = HERE / ".advisor_agent_id"
CONFIG_HASH_FILE = HERE / ".advisor_config_hash"
TIMEOUT_SECONDS = 150

REGIONS = ["upper", "waist", "lower_body", "hem", "whole"]

INSTRUCTIONS = f"""You are a product advisor for Amazon sellers of fashion and accessories.

You receive JSON with: the product, buyer complaints (from competing listings), and the seller's
unit economics. Propose 2 or 3 small, practical changes to the finished product that a supplier
could make (a lining, an extra size, better stitching, a bundle, packaging). Each change must
address a real complaint in the input. Do not invent complaints.

Reply with ONLY one JSON object, no prose, no code fences, in exactly this shape:
{{
  "summary": "one sentence on the main opportunity",
  "improvements": [
    {{
      "title": "short name of the change",
      "fixes": "the complaint it addresses, in the buyers' words",
      "region": one of {REGIONS} (where on the product the change shows),
      "unit_cost_delta": number, extra supplier cost per unit in USD (a rough estimate),
      "price_delta": number, extra price per unit in USD buyers would plausibly accept,
      "confidence": "low" | "medium" | "high",
      "risk": "one sentence on what could go wrong",
      "supplier_ask": "one sentence to send to a supplier to request this change"
    }}
  ]
}}
Numbers are estimates, so keep them modest and realistic. Never promise results."""

AGENT_CONFIG: dict[str, Any] = {
    "persona": {"docs": [{"name": "AGENTS.md", "content": INSTRUCTIONS}]},
}

SAMPLE_ANSWER: dict[str, Any] = {
    "summary": "Buyers want a fuller cut and an opaque fabric; a lining and extra sizes address both.",
    "improvements": [
        {
            "title": "Add a full lining",
            "fixes": "see-through, especially in white",
            "region": "lower_body",
            "unit_cost_delta": 0.9,
            "price_delta": 3.0,
            "confidence": "medium",
            "risk": "Adds weight and may feel warmer in summer.",
            "supplier_ask": "Can you add a breathable cotton lining to the pants and quote the extra cost?",
        },
        {
            "title": "Extend the size range up to 3XL",
            "fixes": "runs small",
            "region": "whole",
            "unit_cost_delta": 0.4,
            "price_delta": 1.0,
            "confidence": "medium",
            "risk": "More SKUs to stock and a higher minimum order.",
            "supplier_ask": "Can you produce sizes up to 3XL, and what is the minimum order per size?",
        },
        {
            "title": "Elastic waistband with an inner drawcord",
            "fixes": "waistband loosens after washing",
            "region": "waist",
            "unit_cost_delta": 0.3,
            "price_delta": 1.0,
            "confidence": "low",
            "risk": "Changes the look at the waist.",
            "supplier_ask": "Can you use a stronger elastic with an inner drawcord on the waistband?",
        },
    ],
    "source": "sample",
}


def _fingerprint() -> str:
    return hashlib.sha256(json.dumps(AGENT_CONFIG, sort_keys=True).encode()).hexdigest()


def _parse(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON in the agent's reply")
    data = json.loads(text[start : end + 1])
    cleaned = []
    for item in data.get("improvements") or []:
        region = item.get("region") if item.get("region") in REGIONS else "whole"
        cleaned.append(
            {
                "title": str(item.get("title", "Change")).strip(),
                "fixes": str(item.get("fixes", "")).strip(),
                "region": region,
                "unit_cost_delta": max(0.0, float(item.get("unit_cost_delta", 0))),
                "price_delta": max(0.0, float(item.get("price_delta", 0))),
                "confidence": item.get("confidence") if item.get("confidence") in ("low", "medium", "high") else "low",
                "risk": str(item.get("risk", "")).strip(),
                "supplier_ask": str(item.get("supplier_ask", "")).strip(),
            }
        )
    if not cleaned:
        raise ValueError("the agent returned no improvements")
    return {"summary": str(data.get("summary", "")).strip(), "improvements": cleaned[:3]}


async def _get_or_create_agent(client: Any) -> str:
    fingerprint = _fingerprint()
    if AGENT_ID_FILE.exists():
        agent_id = AGENT_ID_FILE.read_text().strip()
        stored = CONFIG_HASH_FILE.read_text().strip() if CONFIG_HASH_FILE.exists() else ""
        if stored != fingerprint:
            await client.update_agent(agent_id, AGENT_CONFIG)
            CONFIG_HASH_FILE.write_text(fingerprint + "\n")
        return agent_id

    models = await client.list_models()
    selectable = [m for m in models if m.get("selectable") is not False]
    if not selectable:
        raise RuntimeError("No selectable ZooWork model")
    chosen = next((m for m in selectable if "model" in (m.get("default_for") or [])), selectable[0])
    resource = {"name": "product-advisor", "model": {"primary": chosen["model"]}, **AGENT_CONFIG}
    created = await client.create_agent(resource, idempotency_key="product-advisor-agent-v1")
    agent_id = created["agent_id"]
    AGENT_ID_FILE.write_text(agent_id + "\n")
    CONFIG_HASH_FILE.write_text(fingerprint + "\n")
    return agent_id


async def _advise(payload: dict[str, Any]) -> dict[str, Any]:
    async with create_zoowork_client() as client:
        agent_id = await _get_or_create_agent(client)
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)
        session = await client.create_session(
            agent_id,
            {"initial_events": [{"type": "user.message", "content": json.dumps(payload)}]},
        )
        text = ""
        async for event in client.stream_events(agent_id, session["session_id"]):
            text += assistant_text(event)
            if is_run_finished(event):
                if run_outcome(event) != "succeeded":
                    raise RuntimeError(f"run ended: {run_outcome(event)}")
                break
        return _parse(text)


def advise(product: str, complaints: list[str], economics: dict[str, Any]) -> dict[str, Any]:
    """Blocking call. Returns the advisor's answer, or a labelled sample if anything fails."""
    payload = {"product": product, "buyer_complaints": complaints, "unit_economics": economics}
    try:
        result = asyncio.run(asyncio.wait_for(_advise(payload), TIMEOUT_SECONDS))
        return {**result, "source": "zoowork_agent"}
    except Exception as exc:  # keep the demo alive, but say so
        message = re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}")[:200]
        return {**SAMPLE_ANSWER, "note": f"Agent unavailable ({message}); showing a sample answer."}
