"""Sketch agent: a ZooWork Managed Agent that draws the improved product as a fashion flat (SVG).

draw_improved(spec, changes) -> {"svg": str | None, "source": "zoowork_agent" | "none", "note"?}

None of the account's models generate photos, but they can write SVG. The agent draws a clean
front-view flat sketch (the line drawing designers send factories) of the product WITH the
selected changes applied, and highlights each changed area with a numbered green marker. It is
an illustration, not a photo. The SVG is checked and stripped of anything active (scripts,
event handlers, external links) before it is shown.

State files (ignored by git): agent/.sketch_agent_id and agent/.sketch_config_hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from zoowork import assistant_text, create_zoowork_client, is_run_finished, run_outcome

HERE = Path(__file__).resolve().parent
AGENT_ID_FILE = HERE / ".sketch_agent_id"
CONFIG_HASH_FILE = HERE / ".sketch_config_hash"
TIMEOUT_SECONDS = 150
MAX_SVG_CHARS = 60_000

INSTRUCTIONS = """You are a fashion technical illustrator. You draw clean front-view FLAT SKETCHES
(the line drawings designers send to factories) as SVG.

You receive JSON with a product (type, title, materials, features, colours if known) and a list
of numbered changes. Draw the product AFTER the changes, so each change is clearly visible in the
drawing (for example a smaller pocket flap, ribbed elastic cuffs, a lining edge showing at the
hem, an added drawcord, a matching storage bag drawn next to the garment).

Rules:
- Reply with ONLY one <svg> element, nothing before or after it, no code fences.
- Use viewBox="0 0 600 700", a white background rect, dark grey outlines (stroke #333,
  stroke-width 2 to 3), soft fills in the product's colours, simple seams and stitch lines
  (stroke-dasharray) so it reads as a garment flat.
- For each change, draw it in the garment, then outline that area with a green dashed shape
  (stroke #00a374, stroke-dasharray "8 6", no fill) and put a green circle (r 16, fill #00a374)
  with the change number in white bold text next to it.
- No other text, no <script>, no <foreignObject>, no <image>, no links, no external resources.
- Keep it under 40 KB."""

MODEL = {"primary": "litellm/claude-sonnet-5-5", "thinking": "low"}
AGENT_CONFIG: dict[str, Any] = {
    "model": MODEL,
    "persona": {"docs": [{"name": "AGENTS.md", "content": INSTRUCTIONS}]},
}

SVG_NS = "http://www.w3.org/2000/svg"
BANNED_TAGS = {"script", "foreignobject", "image", "a", "iframe", "object", "embed", "use"}


def clean_svg(text: str) -> str:
    """Extract the <svg> and keep only passive drawing markup. Raises ValueError if unusable."""
    start, end = text.find("<svg"), text.rfind("</svg>")
    if start == -1 or end == -1:
        raise ValueError("no <svg> in the reply")
    raw = text[start : end + len("</svg>")]
    if len(raw) > MAX_SVG_CHARS:
        raise ValueError("sketch too large")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"invalid SVG ({exc})") from None

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    if local(root.tag) != "svg":
        raise ValueError("root is not <svg>")
    for parent in list(root.iter()):
        for child in list(parent):
            if local(child.tag) in BANNED_TAGS:
                parent.remove(child)
    for el in root.iter():
        for attr in list(el.attrib):
            name = local(attr)
            value = el.attrib[attr].strip().lower()
            if name.startswith("on") or (name == "href" and not value.startswith("#")) or "javascript:" in value \
                    or ("url(" in value and "url(#" not in value):
                del el.attrib[attr]
    ET.register_namespace("", SVG_NS)
    if not root.get("viewBox"):
        root.set("viewBox", "0 0 600 700")
    return ET.tostring(root, encoding="unicode")


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
    created = await client.create_agent({"name": "product-sketch", **AGENT_CONFIG},
                                        idempotency_key="product-sketch-agent-v1")
    agent_id = created["agent_id"]
    AGENT_ID_FILE.write_text(agent_id + "\n")
    CONFIG_HASH_FILE.write_text(fingerprint + "\n")
    return agent_id


async def _draw(payload: dict[str, Any]) -> str:
    async with create_zoowork_client() as client:
        agent_id = await _get_or_create_agent(client)
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)
        session = await client.create_session(
            agent_id, {"initial_events": [{"type": "user.message", "content": json.dumps(payload)}]}
        )
        text = ""
        async for event in client.stream_events(agent_id, session["session_id"]):
            text += assistant_text(event)
            if is_run_finished(event):
                if run_outcome(event) != "succeeded":
                    raise RuntimeError(f"run ended: {run_outcome(event)}")
                break
        return clean_svg(text)


def draw_improved(spec: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Blocking. Returns {"svg", "source"} or {"svg": None, "note"} when drawing fails."""
    payload = {
        "product": {k: spec.get(k) for k in ("product_type", "title", "materials", "features")},
        "changes": [{"number": c.get("n", i), "title": c["title"], "fixes": c.get("fixes", ""),
                     "where": c.get("region", "whole")} for i, c in enumerate(changes, 1)],
    }
    try:
        svg = asyncio.run(asyncio.wait_for(_draw(payload), TIMEOUT_SECONDS))
        return {"svg": svg, "source": "zoowork_agent"}
    except Exception as exc:
        message = re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}")[:160]
        return {"svg": None, "source": "none", "note": f"Sketch unavailable ({message})."}
