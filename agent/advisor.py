"""Product advisor: a ZooWork Managed Agent that turns evidence into product changes.

advise_evidence(product, evidence, economics, market) reads counted review themes and real
review excerpts (tools/reviews.py), proposes changes, and must cite theme names and review ids
that exist in that evidence. Anything it cites that does not exist is dropped, and the counts
shown to a person are computed by tools/reviews.support(), never by the model.

advise(product, complaints, economics) is the older free-text version (pasted complaints).

Needs ZOOWORK_API_KEY. If the agent fails, a labelled fallback is returned so the demo keeps
working: rule-based changes from the same evidence (advise_evidence) or a sample (advise).

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

from tools.reviews import prompt_pack, rule_based, support

HERE = Path(__file__).resolve().parent
AGENT_ID_FILE = HERE / ".advisor_agent_id"
CONFIG_HASH_FILE = HERE / ".advisor_config_hash"
TIMEOUT_SECONDS = 150

REGIONS = ["upper", "waist", "lower_body", "hem", "whole"]

INSTRUCTIONS = """You are a product advisor for Amazon sellers of fashion and accessories.

Each message gives you data and the exact JSON shape to answer with. Propose 2 or 3 small,
practical changes to the finished product that a supplier could make (a lining, a fabric
finish, a size-chart fix, better stitching, a bundle, packaging). Base every change on the data
you are given; never invent complaints, numbers, themes or review ids.

Reply with ONLY one JSON object in the requested shape: no prose, no code fences.
Dollar amounts are rough estimates, so keep them modest and realistic. Never promise results."""

AGENT_CONFIG: dict[str, Any] = {
    "persona": {"docs": [{"name": "AGENTS.md", "content": INSTRUCTIONS}]},
}

TEXT_SHAPE = f"""{{
  "summary": "one sentence on the main opportunity",
  "improvements": [
    {{
      "title": "short name of the change",
      "fixes": "the complaint it addresses, in the buyers' words",
      "region": one of {REGIONS} (where on the product the change shows),
      "unit_cost_delta": number, extra supplier cost per unit in USD (rough estimate),
      "price_delta": number, extra price per unit in USD buyers would plausibly accept,
      "confidence": "low" | "medium" | "high",
      "risk": "one sentence on what could go wrong",
      "supplier_ask": "one sentence to send to a supplier to request this change"
    }}
  ]
}}"""

EVIDENCE_SHAPE = f"""{{
  "summary": "one sentence on the main opportunity",
  "improvements": [
    {{
      "title": "short name of the change",
      "observation": "what the reviews show, in your own words. Do not state counts or percentages",
      "themes": ["exact theme names from the evidence that this change addresses"],
      "review_ids": ["1 to 3 ids of reviews that best show the problem, from the evidence"],
      "region": one of {REGIONS} (where on the product the change shows),
      "unit_cost_delta": number, extra supplier cost per unit in USD (rough estimate),
      "price_delta": number, extra price per unit in USD buyers would plausibly accept,
      "confidence": "low" | "medium" | "high",
      "risk": "one sentence on what could go wrong, including anything buyers like that the change might hurt",
      "supplier_ask": "one sentence to send to a supplier to request this change"
    }}
  ]
}}"""

SAMPLE_ANSWER: dict[str, Any] = {
    "summary": "Buyers want a fuller cut and an opaque fabric; a lining and extra sizes address both.",
    "improvements": [
        {"title": "Add a full lining", "fixes": "see-through, especially in white", "region": "lower_body",
         "unit_cost_delta": 0.9, "price_delta": 3.0, "confidence": "medium",
         "risk": "Adds weight and may feel warmer in summer.",
         "supplier_ask": "Can you add a breathable cotton lining to the pants and quote the extra cost?"},
        {"title": "Extend the size range up to 3XL", "fixes": "runs small", "region": "whole",
         "unit_cost_delta": 0.4, "price_delta": 1.0, "confidence": "medium",
         "risk": "More SKUs to stock and a higher minimum order.",
         "supplier_ask": "Can you produce sizes up to 3XL, and what is the minimum order per size?"},
        {"title": "Elastic waistband with an inner drawcord", "fixes": "waistband loosens after washing",
         "region": "waist", "unit_cost_delta": 0.3, "price_delta": 1.0, "confidence": "low",
         "risk": "Changes the look at the waist.",
         "supplier_ask": "Can you use a stronger elastic with an inner drawcord on the waistband?"},
    ],
    "source": "sample",
}


# ---------- plumbing ----------

def _fingerprint() -> str:
    return hashlib.sha256(json.dumps(AGENT_CONFIG, sort_keys=True).encode()).hexdigest()


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


async def _ask(prompt: str) -> str:
    async with create_zoowork_client() as client:
        agent_id = await _get_or_create_agent(client)
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)
        session = await client.create_session(
            agent_id, {"initial_events": [{"type": "user.message", "content": prompt}]}
        )
        text = ""
        async for event in client.stream_events(agent_id, session["session_id"]):
            text += assistant_text(event)
            if is_run_finished(event):
                if run_outcome(event) != "succeeded":
                    raise RuntimeError(f"run ended: {run_outcome(event)}")
                break
        return text


def _json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON in the agent's reply")
    return json.loads(text[start : end + 1])


def _common(item: dict[str, Any]) -> dict[str, Any]:
    confidence = item.get("confidence")
    return {
        "title": str(item.get("title", "Change")).strip(),
        "region": item.get("region") if item.get("region") in REGIONS else "whole",
        "unit_cost_delta": max(0.0, float(item.get("unit_cost_delta", 0))),
        "price_delta": max(0.0, float(item.get("price_delta", 0))),
        "confidence": confidence if confidence in ("low", "medium", "high") else "low",
        "risk": str(item.get("risk", "")).strip(),
        "supplier_ask": str(item.get("supplier_ask", "")).strip(),
    }


def _note(exc: Exception) -> str:
    return re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}")[:200]


# ---------- grounded in review evidence ----------

def _parse_grounded(text: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep only changes backed by real themes or reviews; attach counts computed from the data."""
    data = _json(text)
    known_themes = {t["name"].lower(): t["name"] for t in evidence["themes"]}
    for r in evidence["reviews"].values():
        for t in r["themes"]:
            known_themes.setdefault(t.lower(), t)
    kept = []
    for item in data.get("improvements") or []:
        themes = [known_themes[t.lower()] for t in item.get("themes") or [] if str(t).lower() in known_themes]
        ids = [i for i in item.get("review_ids") or [] if i in evidence["reviews"]]
        if not themes and not ids:
            continue  # nothing real behind it
        sup = support(evidence, themes, ids)
        if sup["count"] == 0:
            continue
        kept.append(
            {**_common(item), "observation": str(item.get("observation", "")).strip(),
             "themes": themes, "review_ids": ids, "support": sup}
        )
    if not kept:
        raise ValueError("the agent cited no real themes or reviews")
    return {"summary": str(data.get("summary", "")).strip(), "improvements": kept[:3]}


def advise_evidence(
    product: str, evidence: dict[str, Any], economics: dict[str, Any], market: dict[str, Any]
) -> dict[str, Any]:
    """Blocking. Changes grounded in review evidence, each with computed support."""
    payload = {
        "product": product,
        "market": {"median_price": market["price"]["median"], "p75_price": market["price"]["p75"]},
        "unit_economics": economics,
        "evidence": prompt_pack(evidence),
    }
    prompt = (
        "Task: propose 2 or 3 product changes backed by the review evidence.\n"
        "Rules: cite only theme names and review ids that appear in the evidence; group related themes; "
        "keep what buyers like (what_buyers_like). price_delta is what a buyer would plausibly pay extra "
        "for that fix; a strong fix of a top complaint can justify a modest premium. Do not return 0 "
        "unless buyers clearly would not pay more. The app warns the seller if the new price goes above "
        "the market's 75th percentile, so keep premiums realistic.\n"
        f"Answer with exactly this JSON shape:\n{EVIDENCE_SHAPE}\n\nDATA:\n{json.dumps(payload)}"
    )
    try:
        result = _parse_grounded(asyncio.run(asyncio.wait_for(_ask(prompt), TIMEOUT_SECONDS)), evidence)
        return {**result, "source": "zoowork_agent"}
    except Exception as exc:  # keep the demo alive, but say so
        fallback = []
        for item in rule_based(evidence):
            fallback.append({**item, "support": support(evidence, item["themes"], item["review_ids"])})
        return {
            "summary": "Rule-based changes from the most common review themes.",
            "improvements": fallback,
            "source": "rule_based",
            "note": f"Agent unavailable ({_note(exc)}); showing rule-based changes from the same reviews.",
        }


# ---------- older free-text version ----------

def _parse_text(text: str) -> dict[str, Any]:
    data = _json(text)
    cleaned = [
        {**_common(item), "fixes": str(item.get("fixes", "")).strip()} for item in data.get("improvements") or []
    ]
    if not cleaned:
        raise ValueError("the agent returned no improvements")
    return {"summary": str(data.get("summary", "")).strip(), "improvements": cleaned[:3]}


def advise(product: str, complaints: list[str], economics: dict[str, Any]) -> dict[str, Any]:
    """Blocking. Changes from pasted complaints. Falls back to a labelled sample."""
    payload = {"product": product, "buyer_complaints": complaints, "unit_economics": economics}
    prompt = f"Answer with exactly this JSON shape:\n{TEXT_SHAPE}\n\nDATA:\n{json.dumps(payload)}"
    try:
        result = _parse_text(asyncio.run(asyncio.wait_for(_ask(prompt), TIMEOUT_SECONDS)))
        return {**result, "source": "zoowork_agent"}
    except Exception as exc:
        return {**SAMPLE_ANSWER, "note": f"Agent unavailable ({_note(exc)}); showing a sample answer."}
