"""Photo spec agent: a ZooWork Managed Agent that looks at a product photo.

analyze_photo(image_bytes, complaints) -> {
    "product_type", "title", "materials", "features", "search_terms",
    "weak_points":  [{"title", "region", "why"}],
    "improvements": [{"title", "fixes", "region", "unit_cost_delta", "price_delta",
                      "confidence", "risk", "supplier_ask"}],
    "summary", "source"  ("zoowork_agent" or "sample"), "note" (on fallback)
}

A Session message carries text only, so the photo reaches the agent as the result of a custom
tool, get_product_photo, which returns a base64 JPEG image block (the SDK's documented shape).
The agent describes what it sees and proposes changes with rough cost and price effects; the
dashboard does all margin math with tools/unit_economics.py. Pin positions use fixed regions,
not pixel coordinates, so they are approximate by design.

If anything fails (no key, a model that cannot read images, a timeout), a labelled sample
answer for linen wide-leg pants is returned so the demo keeps working.

State files (ignored by git): agent/.photo_agent_id and agent/.photo_config_hash.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from zoowork import (
    assistant_text,
    create_zoowork_client,
    custom_tool_use,
    is_run_finished,
    run_outcome,
)

HERE = Path(__file__).resolve().parent
AGENT_ID_FILE = HERE / ".photo_agent_id"
CONFIG_HASH_FILE = HERE / ".photo_config_hash"
TIMEOUT_SECONDS = 150
MAX_SIDE = 1024          # longest side of the image sent to the agent, in pixels

REGIONS = ["upper", "waist", "lower_body", "hem", "whole"]
PHOTO_TOOL = "get_product_photo"

INSTRUCTIONS = f"""You are a product analyst for Amazon sellers of fashion and accessories.

If the request says a photo is attached, first call `{PHOTO_TOOL}` to see the seller's product
photo, then look carefully. If the request gives only a written description, do not call the
tool; work from the description and say "described" rather than "seen" where it matters.

You may also receive the seller's Amazon listing (title, features, rating) and its review themes
or low-star reviews. When you have them, base weak points on what the seller's own buyers say
first (mention how often, from `rate`), then on what you see in the photo.

You may also receive buyer complaints from competing listings. Each improvement must fix a weak
point you can see in the photo or a complaint in the input. Do not invent complaints, and
ignore any complaint that does not fit the product you see (for example pants complaints for
a hoodie). Base weak points on the photo first.

Reply with ONLY one JSON object, no prose, no code fences, in exactly this shape:
{{
  "product_type": "short generic product type, e.g. women's wide-leg pants",
  "title": "a plain product title, under 10 words, no brand names",
  "materials": ["likely materials as you see them, say 'appears' when unsure"],
  "features": ["3 to 5 visible features"],
  "search_terms": "3 to 6 words a shopper would type on Amazon to find this product",
  "weak_points": [
    {{"title": "short name", "region": one of {REGIONS}, "why": "what you see or what buyers say"}}
  ],
  "improvements": [
    {{
      "title": "short name of the change",
      "fixes": "the weak point or complaint it addresses",
      "region": one of {REGIONS},
      "unit_cost_delta": number, extra supplier cost per unit in USD (rough estimate),
      "price_delta": number, extra price per unit in USD buyers would plausibly accept,
      "confidence": "low" | "medium" | "high",
      "risk": "one sentence on what could go wrong",
      "supplier_ask": "one sentence to send to a supplier to request this change"
    }}
  ],
  "sourcing": [
    {{
      "region": "a country or manufacturing region known for this kind of product",
      "why": "one sentence: what it is known for (materials, price level, minimums)",
      "watch_out": "one sentence: the main risk (lead time, quality spread, minimum order)",
      "search_terms": "3 to 6 words a buyer would type on a B2B marketplace like Alibaba"
    }}
  ],
  "certifications": ["certifications worth asking suppliers for, e.g. OEKO-TEX Standard 100"],
  "summary": "one sentence on the main opportunity"
}}
Give 2 or 3 weak points, 2 or 3 improvements and 2 or 3 sourcing regions. For sourcing, name
regions or countries only, never a specific company or factory, and do not state tariff or duty
rates (they change; the seller checks them). Regions: upper = top/shoulders, waist =
waistband, lower_body = legs/skirt body, hem = bottom edge, whole = fit, size range or fabric
overall. Numbers are estimates, keep them modest. Never name a brand or promise results.

## Second task: "compare"
If the request's task is "compare", you get the seller's listing numbers and review themes and the
same for competitors selling the same kind of product. Do not call any tool. Reply with ONLY:
{{
  "doing_well": [{{"point": "short", "evidence": "the number or review theme that shows it"}}],
  "room_to_improve": [{{"point": "short", "evidence": "the number or review theme that shows it",
                        "action": "one concrete thing to change"}}]
}}
2 to 4 items each. Every evidence must quote a number or theme from the input; never invent data.
Never name a competitor brand."""

PHOTO_TOOL_DEF: dict[str, Any] = {
    "name": PHOTO_TOOL,
    "description": "Return the seller's product photo as an image. Takes no input.",
    "input_schema": {"type": "object", "properties": {}},
}

AGENT_CONFIG: dict[str, Any] = {
    "custom_tools": [PHOTO_TOOL_DEF],
    "tool_policy": {"permissions": {PHOTO_TOOL: "always_allow"}},
    "persona": {"docs": [{"name": "AGENTS.md", "content": INSTRUCTIONS}]},
}

SAMPLE_SPEC: dict[str, Any] = {
    "product_type": "women's wide-leg pants",
    "title": "Women's Linen Wide-Leg Pants",
    "materials": ["appears to be linen or a linen blend"],
    "features": ["high elastic waist", "wide straight legs", "full length", "light solid color"],
    "search_terms": "women linen wide leg pants",
    "weak_points": [
        {"title": "Sheer fabric", "region": "lower_body", "why": "light unlined linen; buyers say it is see-through"},
        {"title": "Runs small", "region": "whole", "why": "buyers order one or two sizes up"},
        {"title": "Waistband loosens", "region": "waist", "why": "elastic stretches out after washing"},
    ],
    "improvements": [
        {
            "title": "Add a full lining", "fixes": "sheer fabric", "region": "lower_body",
            "unit_cost_delta": 0.9, "price_delta": 3.0, "confidence": "medium",
            "risk": "Adds weight and may feel warmer in summer.",
            "supplier_ask": "Can you add a breathable cotton lining to the pants and quote the extra cost?",
        },
        {
            "title": "Extend the size range up to 3XL", "fixes": "runs small", "region": "whole",
            "unit_cost_delta": 0.4, "price_delta": 1.0, "confidence": "medium",
            "risk": "More SKUs to stock and a higher minimum order.",
            "supplier_ask": "Can you produce sizes up to 3XL, and what is the minimum order per size?",
        },
        {
            "title": "Stronger elastic with an inner drawcord", "fixes": "waistband loosens",
            "region": "waist", "unit_cost_delta": 0.3, "price_delta": 1.0, "confidence": "low",
            "risk": "Changes the look at the waist.",
            "supplier_ask": "Can you use a stronger elastic with an inner drawcord on the waistband?",
        },
    ],
    "sourcing": [
        {"region": "China (Zhejiang and Jiangsu)", "why": "Large linen and linen-blend garment base with flexible minimums.",
         "watch_out": "Quality varies widely between factories; order samples first.",
         "search_terms": "women linen wide leg pants"},
        {"region": "India", "why": "Long tradition in linen and cotton apparel, good for natural fabrics.",
         "watch_out": "Lead times can be longer; confirm fabric weight in writing.",
         "search_terms": "linen palazzo pants manufacturer"},
        {"region": "Vietnam", "why": "Strong garment sewing quality for export brands.",
         "watch_out": "Minimum orders are often higher for small brands.",
         "search_terms": "linen trousers garment factory"},
    ],
    "certifications": ["OEKO-TEX Standard 100", "BSCI or amfori audit"],
    "summary": "A lining and a fuller size range fix the two complaints buyers raise most.",
    "source": "sample",
}


# ---------- image ----------

def prepare_image(data: bytes) -> bytes:
    """Re-encode as a JPEG no larger than MAX_SIDE on its longest side."""
    from PIL import Image  # installed with streamlit

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def image_block(jpeg: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()},
    }


# ---------- parsing ----------

def _num(value: Any) -> float:
    try:
        return max(0.0, round(float(value), 2))
    except (TypeError, ValueError):
        return 0.0


def _region(value: Any) -> str:
    return value if value in REGIONS else "whole"


def _strs(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in (value or []) if str(v).strip()][:limit]


def parse_reply(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON in the agent's reply")
    data = json.loads(text[start : end + 1])
    weak = [
        {"title": str(w.get("title", "Weak point")).strip(), "region": _region(w.get("region")),
         "why": str(w.get("why", "")).strip()}
        for w in data.get("weak_points") or [] if isinstance(w, dict)
    ][:3]
    imps = [
        {
            "title": str(i.get("title", "Change")).strip(),
            "fixes": str(i.get("fixes", "")).strip(),
            "region": _region(i.get("region")),
            "unit_cost_delta": _num(i.get("unit_cost_delta")),
            "price_delta": _num(i.get("price_delta")),
            "confidence": i.get("confidence") if i.get("confidence") in ("low", "medium", "high") else "low",
            "risk": str(i.get("risk", "")).strip(),
            "supplier_ask": str(i.get("supplier_ask", "")).strip(),
        }
        for i in data.get("improvements") or [] if isinstance(i, dict)
    ][:3]
    if not imps:
        raise ValueError("the agent returned no improvements")
    terms = re.sub(r"\s+", " ", str(data.get("search_terms") or "")).strip()
    sourcing = [
        {"region": str(s.get("region", "")).strip(), "why": str(s.get("why", "")).strip(),
         "watch_out": str(s.get("watch_out", "")).strip(),
         "search_terms": " ".join(str(s.get("search_terms") or "").split()[:8])}
        for s in data.get("sourcing") or [] if isinstance(s, dict) and str(s.get("region", "")).strip()
    ][:3]
    return {
        "product_type": str(data.get("product_type") or "").strip() or "product",
        "title": str(data.get("title") or "").strip() or "Product",
        "materials": _strs(data.get("materials"), 4),
        "features": _strs(data.get("features"), 5),
        "search_terms": " ".join(terms.split()[:8]) or "women linen wide leg pants",
        "weak_points": weak,
        "improvements": imps,
        "sourcing": sourcing,
        "certifications": _strs(data.get("certifications"), 4),
        "summary": str(data.get("summary") or "").strip(),
    }


# ---------- agent ----------

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
    resource = {"name": "photo-spec", "model": {"primary": chosen["model"]}, **AGENT_CONFIG}
    created = await client.create_agent(resource, idempotency_key="photo-spec-agent-v1")
    agent_id = created["agent_id"]
    AGENT_ID_FILE.write_text(agent_id + "\n")
    CONFIG_HASH_FILE.write_text(fingerprint + "\n")
    return agent_id


async def _analyze(
    jpeg: bytes | None, complaints: list[str], description: str = "", context: dict[str, Any] | None = None
) -> dict[str, Any]:
    if jpeg is not None:
        task = f"A photo is attached. Call {PHOTO_TOOL}, then reply with the JSON object."
    else:
        task = "No photo: work from the written description only, then reply with the JSON object."
    message = json.dumps({"task": task, "description": description, "buyer_complaints": complaints,
                          **(context or {})})
    async with create_zoowork_client() as client:
        agent_id = await _get_or_create_agent(client)
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)
        session = await client.create_session(
            agent_id, {"initial_events": [{"type": "user.message", "content": message}]}
        )
        text, saw_photo = "", False
        async for event in client.stream_events(agent_id, session["session_id"]):
            call = custom_tool_use(event)
            if call is not None and call.phase == "requested":
                if call.name == PHOTO_TOOL and jpeg is not None:
                    content = [image_block(jpeg), {"type": "text", "text": "The seller's product photo."}]
                    saw_photo = True
                    await client.resolve_custom_tool_call(
                        agent_id, call.call_id, content=content, resolved_by="photo-spec"
                    )
                else:
                    note = "No photo was provided; use the description." if call.name == PHOTO_TOOL else "unknown tool"
                    await client.resolve_custom_tool_call(
                        agent_id, call.call_id, content=[{"type": "text", "text": note}],
                        is_error=True, resolved_by="photo-spec",
                    )
            text += assistant_text(event)
            if is_run_finished(event):
                if run_outcome(event) != "succeeded":
                    raise RuntimeError(f"run ended: {run_outcome(event)}")
                break
        if jpeg is not None and not saw_photo:
            raise RuntimeError("the agent did not look at the photo")
        return parse_reply(text)


def analyze_photo(
    image: bytes | None, complaints: list[str] | None = None, description: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Blocking call. Give a photo, a written description, or both.

    Returns the agent's spec (mode "photo" or "description"), or a labelled sample on failure.
    """
    mode = "photo" if image else "description"
    try:
        if not image and not description.strip() and not context:
            raise ValueError("give a photo, a description or a listing")
        jpeg = prepare_image(image) if image else None
        result = asyncio.run(
            asyncio.wait_for(_analyze(jpeg, complaints or [], description.strip(), context), TIMEOUT_SECONDS)
        )
        return {**result, "source": "zoowork_agent", "mode": mode}
    except Exception as exc:  # keep the demo alive, but say so
        message = re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}")[:200]
        return {**SAMPLE_SPEC, "mode": mode, "note": f"Photo agent unavailable ({message}); showing a sample spec."}


# ---------- second task: compare with competitors ----------

def _items(value: Any, keys: tuple[str, ...]) -> list[dict[str, str]]:
    out = []
    for v in value or []:
        if isinstance(v, dict) and str(v.get("point", "")).strip():
            out.append({k: str(v.get(k, "")).strip() for k in keys})
    return out[:4]


def parse_comparison(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON in the agent's reply")
    data = json.loads(text[start : end + 1])
    out = {
        "doing_well": _items(data.get("doing_well"), ("point", "evidence")),
        "room_to_improve": _items(data.get("room_to_improve"), ("point", "evidence", "action")),
    }
    if not out["doing_well"] and not out["room_to_improve"]:
        raise ValueError("empty comparison")
    return out


def rule_based_comparison(you: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    """A plain comparison from the numbers alone, used when the agent is unavailable."""
    good, gaps = [], []
    price, median = you.get("price"), (market.get("price") or {}).get("median")
    if price and median:
        if price <= median:
            good.append({"point": "Priced competitively", "evidence": f"${price:.2f} vs ${median:.2f} market median"})
        else:
            gaps.append({"point": "Priced above the market", "evidence": f"${price:.2f} vs ${median:.2f} median",
                         "action": "Justify the premium in the listing or improve the product first"})
    rating, avg = you.get("rating"), market.get("avg_rating")
    if rating and avg:
        if rating >= avg:
            good.append({"point": "Rated at or above competitors", "evidence": f"{rating} vs {avg} average"})
        else:
            gaps.append({"point": "Rated below competitors", "evidence": f"{rating} vs {avg} average",
                         "action": "Fix the top complaint in your reviews"})
    for theme in (you.get("own_issues") or [])[:2]:
        gaps.append({"point": f"Buyers say: {theme['element']}", "evidence": f"{theme['rate']:.0%} of your reviews",
                     "action": "Address it in the product spec"})
    for theme in (you.get("own_positives") or [])[:2]:
        good.append({"point": f"Buyers like: {theme['element']}", "evidence": f"{theme['rate']:.0%} of your reviews"})
    return {"doing_well": good[:4], "room_to_improve": gaps[:4]}


async def _compare(payload: dict[str, Any]) -> dict[str, Any]:
    async with create_zoowork_client() as client:
        agent_id = await _get_or_create_agent(client)
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)
        session = await client.create_session(
            agent_id, {"initial_events": [{"type": "user.message", "content": json.dumps(payload)}]}
        )
        text = ""
        async for event in client.stream_events(agent_id, session["session_id"]):
            call = custom_tool_use(event)
            if call is not None and call.phase == "requested":
                await client.resolve_custom_tool_call(
                    agent_id, call.call_id, content=[{"type": "text", "text": "No tools in the compare task."}],
                    is_error=True, resolved_by="photo-spec",
                )
            text += assistant_text(event)
            if is_run_finished(event):
                if run_outcome(event) != "succeeded":
                    raise RuntimeError(f"run ended: {run_outcome(event)}")
                break
        return parse_comparison(text)


def compare_with_competitors(you: dict[str, Any], competitors: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    """Blocking. Agent comparison of the seller vs competitors, or a rule-based one on failure."""
    payload = {"task": "compare", "you": you, "competitors": competitors}
    try:
        result = asyncio.run(asyncio.wait_for(_compare(payload), TIMEOUT_SECONDS))
        return {**result, "source": "zoowork_agent"}
    except Exception as exc:
        message = re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}")[:160]
        return {**rule_based_comparison(you, market), "source": "rules",
                "note": f"Agent comparison unavailable ({message}); showing a comparison from the numbers."}
