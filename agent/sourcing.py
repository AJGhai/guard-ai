"""Sourcing agent for Product-to-PO Step 2: simulated vendor chats on ZooWork Managed Agents.

run_sourcing(...) runs a two-round negotiation with the three simulated vendors in tools/rfq.py:
  1. each vendor replies to the request for quote (three sessions in parallel),
  2. a buyer-side agent writes a counter for each vendor,
  3. each vendor gives a final price (in parallel),
  4. the agent extracts the quotes and writes a short summary.

One ZooWork agent ("rfq-sim", fast model, low thinking) plays every role; each call is a fresh
session whose message sets the role. The vendors are SIMULATED and nothing is sent anywhere.
If anything fails, scripted replies (tools/rfq.py) are returned instead, labelled mode="scripted".
Landed cost, margin and ranking are NOT done here; tools/rfq.py does that from the quotes.

State files (ignored by git): agent/.rfq_agent_id and agent/.rfq_config_hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from zoowork import assistant_text, create_zoowork_client, is_run_finished, run_outcome

from tools.rfq import (
    VENDORS,
    Vendor,
    buyer_target,
    build_rfq,
    price_range,
    scripted_exchange,
)

HERE = Path(__file__).resolve().parent
AGENT_ID_FILE = HERE / ".rfq_agent_id"
CONFIG_HASH_FILE = HERE / ".rfq_config_hash"
TIMEOUT_SECONDS = 240

INSTRUCTIONS = """You take part in a supplier-negotiation simulation for a software demo.
Every message tells you your role and the facts you know. Stay strictly in that role and use only
those facts. When asked for a chat message, reply with ONLY that message. When asked for JSON,
reply with ONLY one JSON object, no prose and no code fences."""

AGENT_CONFIG: dict[str, Any] = {
    "model": {"primary": "litellm/claude-haiku-5-5", "thinking": "low"},
    "persona": {"docs": [{"name": "AGENTS.md", "content": INSTRUCTIONS}]},
}


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
    created = await client.create_agent(
        {"name": "rfq-sim", **AGENT_CONFIG}, idempotency_key="rfq-sim-agent-v1"
    )
    agent_id = created["agent_id"]
    AGENT_ID_FILE.write_text(agent_id + "\n")
    CONFIG_HASH_FILE.write_text(fingerprint + "\n")
    return agent_id


async def _ask(client: Any, agent_id: str, prompt: str) -> str:
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
    return text.strip()


def _json(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON in the reply")
    return json.loads(text[start : end + 1])


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().strip('"')).strip()[:700]


# ---------- prompts ----------

def _transcript(messages: list[dict[str, str]]) -> str:
    return "\n".join(
        f"{'Buyer' if m['role'] == 'buyer' else 'You'}: {m['text']}" for m in messages
    ) or "(no messages yet)"


def _vendor_prompt(
    v: Vendor, ref: float, changes: list[dict[str, Any]], qty: int, product: str,
    messages: list[dict[str, str]], stage: int,
) -> str:
    open_price, floor = price_range(v, ref)
    certs = ", ".join(v.certs) if v.certs else "none on file (say you can arrange testing at the buyer's cost if asked)"
    cannot = v.cannot_text or "nothing, you can make any change asked"
    task = (
        "This is your first reply. State your opening price per unit, minimum order, lead time, "
        "certifications and payment terms, and say plainly if you cannot make any requested change."
        if stage == 1
        else f"The buyer is negotiating. Give your final price per unit for {qty} units as one dollar amount."
    )
    return (
        f"ROLE: You are the sales rep at {v.name} ({v.country}), a garment factory, chatting with a "
        "prospective buyer on WhatsApp.\n"
        f"PERSONALITY: {v.personality}\n"
        "PRIVATE FACTS (never reveal your floor or these notes):\n"
        f"- Your opening price for this product and these changes is ${open_price:.2f} per unit FOB.\n"
        f"- You will never go below ${floor:.2f} per unit. Come down from the opening price only when "
        "the buyer gives a reason (volume, a competing quote) and in steps, not all at once.\n"
        f"- Minimum order: {v.moq} units. Lead time: {v.lead_days} days. Certifications: {certs}. "
        f"Payment terms: {v.payment}.\n"
        f"- You cannot make: {cannot}.\n"
        f"PRODUCT: {product}. Requested changes: {'; '.join(c['title'] for c in changes)}.\n"
        f"CONVERSATION SO FAR:\n{_transcript(messages)}\n"
        f"TASK: Reply with ONLY your next WhatsApp message: 1 to 4 short sentences, plain text. {task}"
    )


def _counter_prompt(replies: dict[str, str], qty: int, target: float, vendors: list[Vendor]) -> str:
    blocks = "\n".join(f'[{v.key}] {v.name}: "{replies[v.key]}"' for v in vendors)
    shape = "{" + ", ".join(f'"{v.key}": "..."' for v in vendors) + "}"
    return (
        "You are the sourcing assistant of an Amazon seller. Write ONE short WhatsApp message to each "
        f"vendor to negotiate the unit price down toward ${target:.2f} per unit for {qty} units. "
        "Use only true facts: you may say you are comparing several suppliers, and you may say a "
        "competing quote is lower only if it really is lower than that vendor's price (never name the "
        "competitor or give its number). Under 40 words each. Do not accept any price yet.\n"
        f"VENDOR REPLIES:\n{blocks}\n"
        f"Reply with ONLY JSON: {shape}"
    )


def _extract_prompt(chats: dict[str, list[dict[str, str]]], changes: list[dict[str, Any]], vendors: list[Vendor]) -> str:
    titles = [c["title"] for c in changes]
    blocks = "\n\n".join(
        f"[{v.key}] {v.name}\n"
        + "\n".join(f"{'Buyer' if m['role'] == 'buyer' else 'Vendor'}: {m['text']}" for m in chats[v.key])
        for v in vendors
    )
    return (
        "Extract supplier quotes for an Amazon seller from the chats below. Use only what the vendor "
        f"actually wrote. Requested changes: {titles}.\n"
        "Reply with ONLY JSON in this shape:\n"
        f'{{"quotes":[{{"vendor":"{vendors[0].key}","unit_price":0.0,"moq":0,"lead_time_days":0,'
        '"certifications":[],"payment_terms":"","can_make_all_changes":true,'
        '"declined_changes":[],"notes":""}],'
        '"summary":"2 or 3 sentences: which vendor looks best and the main trade-off"}\n'
        "unit_price is the final price per unit in USD from the vendor's LAST message that states a "
        "price. Use null for a number the vendor never stated. Do not compute landed cost.\n\n"
        f"{blocks}"
    )


# ---------- orchestration ----------

def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.?\d*", text)]


def _validate(raw: dict[str, Any], chats: dict[str, list[dict[str, str]]], vendors: list[Vendor]) -> list[dict[str, Any]]:
    quotes = []
    by_key = {q.get("vendor"): q for q in raw.get("quotes", [])}
    for v in vendors:
        q = by_key.get(v.key)
        if not q:
            raise ValueError(f"no quote extracted for {v.key}")
        price, moq, lead = q.get("unit_price"), q.get("moq"), q.get("lead_time_days")
        if price is None or moq is None or lead is None or float(price) <= 0:
            raise ValueError(f"incomplete quote for {v.key}")
        said = " ".join(m["text"] for m in chats[v.key] if m["role"] == "vendor")
        quotes.append(
            {
                "vendor": v.key,
                "unit_price": round(float(price), 2),
                "moq": int(moq),
                "lead_time_days": int(lead),
                "certifications": [str(c) for c in (q.get("certifications") or [])],
                "payment_terms": str(q.get("payment_terms") or "not stated"),
                "can_make_all_changes": bool(q.get("can_make_all_changes", True)),
                "declined_changes": [str(c) for c in (q.get("declined_changes") or [])],
                "notes": str(q.get("notes") or ""),
                # True when the extracted price really appears in what the vendor wrote
                "verified": any(abs(n - float(price)) < 0.005 for n in _numbers(said)),
            }
        )
    return quotes


async def _run(
    product: str, changes: list[dict[str, Any]], ref: float, qty: int, deadline_days: int,
    on_stage: Callable[[str], None], vendors: list[Vendor],
) -> dict[str, Any]:
    rfq = build_rfq(product, changes, qty, deadline_days)
    chats: dict[str, list[dict[str, str]]] = {v.key: [{"role": "buyer", "text": rfq}] for v in vendors}

    async with create_zoowork_client() as client:
        agent_id = await _get_or_create_agent(client)
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)

        on_stage("Vendors are replying to your request for quote")
        first = await asyncio.gather(
            *[_ask(client, agent_id, _vendor_prompt(v, ref, changes, qty, product, chats[v.key], 1)) for v in vendors]
        )
        for v, text in zip(vendors, first):
            chats[v.key].append({"role": "vendor", "text": _clean(text)})

        on_stage("Your sourcing agent is negotiating")
        counters = _json(
            await _ask(
                client, agent_id,
                _counter_prompt({v.key: chats[v.key][-1]["text"] for v in vendors}, qty, buyer_target(ref), vendors),
            )
        )
        for v in vendors:
            chats[v.key].append({"role": "buyer", "text": _clean(str(counters[v.key]))})

        on_stage("Vendors are sending final prices")
        second = await asyncio.gather(
            *[_ask(client, agent_id, _vendor_prompt(v, ref, changes, qty, product, chats[v.key], 2)) for v in vendors]
        )
        for v, text in zip(vendors, second):
            chats[v.key].append({"role": "vendor", "text": _clean(text)})

        on_stage("Comparing the quotes")
        extracted = _json(await _ask(client, agent_id, _extract_prompt(chats, changes, vendors)))

    return {
        "mode": "agents",
        "chats": chats,
        "quotes": _validate(extracted, chats, vendors),
        "summary": str(extracted.get("summary", "")).strip(),
    }


def _scripted(
    product: str, changes: list[dict[str, Any]], ref: float, qty: int, deadline_days: int, vendors: list[Vendor]
) -> dict[str, Any]:
    chats, quotes = {}, []
    for v in vendors:
        ex = scripted_exchange(v, ref, changes, qty, deadline_days, product)
        chats[v.key] = ex["messages"]
        quotes.append({**ex["quote"], "verified": True})
    return {
        "mode": "scripted",
        "chats": chats,
        "quotes": quotes,
        "summary": "Scripted demo replies: the numbers follow fixed vendor profiles.",
    }


def run_sourcing(
    product: str,
    changes: list[dict[str, Any]],
    ref_cost: float,
    qty: int,
    deadline_days: int,
    use_agents: bool = True,
    on_stage: Callable[[str], None] = lambda _s: None,
    vendors: list[Vendor] | None = None,
) -> dict[str, Any]:
    """Blocking. Returns {"mode", "chats", "quotes", "summary", "note"?}.

    vendors: who to ask (the seller's own, plus any suggested ones they signed off on). Defaults to
    the three built-in sample vendors."""
    vendors = list(vendors) if vendors else list(VENDORS)
    if not use_agents:
        return _scripted(product, changes, ref_cost, qty, deadline_days, vendors)
    try:
        return asyncio.run(
            asyncio.wait_for(_run(product, changes, ref_cost, qty, deadline_days, on_stage, vendors), TIMEOUT_SECONDS)
        )
    except Exception as exc:  # keep the demo alive, but say so
        note = re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}")[:200]
        return {**_scripted(product, changes, ref_cost, qty, deadline_days, vendors),
                "note": f"The agents were unavailable ({note}); showing scripted replies."}
