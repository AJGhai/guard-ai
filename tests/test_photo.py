"""Offline tests for the photo spec agent, the spec sheet and the pins (no ZooWork, no network).

Run: .venv/bin/python tests/test_photo.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.photo_spec as ps  # noqa: E402
from tools.spec_sheet import build_spec_sheet, pin_positions, sample_photo  # noqa: E402
from tools.unit_economics import Inputs, compute  # noqa: E402

GOOD_REPLY = {
    "product_type": "women's wide-leg pants", "title": "Linen Wide-Leg Pants",
    "materials": "appears linen", "features": ["elastic waist", "wide legs"],
    "search_terms": "women   linen wide leg pants",
    "weak_points": [{"title": "Sheer", "region": "lower_body", "why": "light fabric"},
                    {"title": "Odd", "region": "elbow", "why": "bad region"}],
    "improvements": [{"title": "Lining", "fixes": "sheer", "region": "lower_body",
                      "unit_cost_delta": "0.9", "price_delta": -2, "confidence": "sure",
                      "risk": "warm", "supplier_ask": "add lining?"}],
    "sourcing": [{"region": "China (Zhejiang)", "why": "linen base", "watch_out": "quality spread",
                  "search_terms": "linen   wide leg pants"}, {"region": "", "why": "no region, dropped"}],
    "certifications": ["OEKO-TEX Standard 100"],
    "summary": "Line it.",
}


class ParseTests(unittest.TestCase):
    def test_parses_and_cleans(self):
        out = ps.parse_reply("Here you go:\n" + json.dumps(GOOD_REPLY) + "\nthanks")
        self.assertEqual(out["search_terms"], "women linen wide leg pants")
        self.assertEqual(out["materials"], ["appears linen"])          # string -> list
        self.assertEqual(out["weak_points"][1]["region"], "whole")      # unknown region -> whole
        imp = out["improvements"][0]
        self.assertEqual((imp["unit_cost_delta"], imp["price_delta"]), (0.9, 0.0))  # no negatives
        self.assertEqual(imp["confidence"], "low")                      # unknown -> low

    def test_sourcing_regions_parsed_and_blank_dropped(self):
        out = ps.parse_reply(json.dumps(GOOD_REPLY))
        self.assertEqual([r["region"] for r in out["sourcing"]], ["China (Zhejiang)"])
        self.assertEqual(out["sourcing"][0]["search_terms"], "linen wide leg pants")
        self.assertEqual(out["certifications"], ["OEKO-TEX Standard 100"])
        no_src = ps.parse_reply(json.dumps({k: v for k, v in GOOD_REPLY.items() if k != "sourcing"}))
        self.assertEqual(no_src["sourcing"], [])

    def test_sample_has_sourcing(self):
        self.assertTrue(ps.SAMPLE_SPEC["sourcing"])
        self.assertTrue(all("factory" not in r["region"].lower() for r in ps.SAMPLE_SPEC["sourcing"]))

    def test_rejects_no_json_or_no_improvements(self):
        with self.assertRaises(ValueError):
            ps.parse_reply("no json here")
        with self.assertRaises(ValueError):
            ps.parse_reply(json.dumps({**GOOD_REPLY, "improvements": []}))


class ImageTests(unittest.TestCase):
    def test_prepare_image_shrinks_and_block_shape(self):
        from PIL import Image
        import io
        big = io.BytesIO()
        Image.new("RGB", (3000, 1500), (200, 10, 10)).save(big, format="PNG")
        jpeg = ps.prepare_image(big.getvalue())
        self.assertEqual(Image.open(io.BytesIO(jpeg)).size, (1024, 512))
        block = ps.image_block(jpeg)
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["media_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(block["source"]["data"]), jpeg)

    def test_sample_photo_is_a_jpeg(self):
        self.assertTrue(sample_photo().startswith(b"\xff\xd8"))


class FakeClient:
    """Stands in for the ZooWork client: the agent asks for the photo, then replies."""

    def __init__(self, reply: str, ask_for_photo: bool = True):
        self.reply, self.ask = reply, ask_for_photo
        self.resolved: list[dict] = []
        self.message = None

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None
    async def start_agent(self, agent_id): return {}
    async def wait_until_running(self, agent_id, timeout=0): return {}

    async def create_session(self, agent_id, body):
        self.message = json.loads(body["initial_events"][0]["content"])
        return {"session_id": "s1"}

    async def resolve_custom_tool_call(self, agent_id, call_id, content, is_error=None, resolved_by=None):
        self.resolved.append({"call_id": call_id, "content": content, "is_error": is_error})
        return {}

    async def stream_events(self, agent_id, session_id):
        if self.ask:
            yield SimpleNamespace(kind="tool", name=ps.PHOTO_TOOL)
        yield SimpleNamespace(kind="text", text=self.reply)
        yield SimpleNamespace(kind="done")


def _patch(fake: FakeClient):
    ps.create_zoowork_client = lambda: fake
    ps.custom_tool_use = lambda e: (
        SimpleNamespace(phase="requested", name=e.name, call_id="c1") if e.kind == "tool" else None
    )
    ps.assistant_text = lambda e: e.text if e.kind == "text" else ""
    ps.is_run_finished = lambda e: e.kind == "done"
    ps.run_outcome = lambda e: "succeeded"

    async def agent_id(client):
        return "agt_test"
    ps._get_or_create_agent = agent_id


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(ps, k) for k in
                       ("create_zoowork_client", "custom_tool_use", "assistant_text",
                        "is_run_finished", "run_outcome", "_get_or_create_agent")}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(ps, k, v)

    def test_photo_goes_back_as_image_block(self):
        fake = FakeClient(json.dumps(GOOD_REPLY))
        _patch(fake)
        out = ps.analyze_photo(sample_photo(), ["runs small"])
        self.assertEqual((out["source"], out["mode"]), ("zoowork_agent", "photo"))
        content = fake.resolved[0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertIsNone(fake.resolved[0]["is_error"])
        self.assertIn("photo is attached", fake.message["task"])
        self.assertEqual(fake.message["buyer_complaints"], ["runs small"])

    def test_agent_that_skips_the_photo_falls_back_to_sample(self):
        _patch(FakeClient(json.dumps(GOOD_REPLY), ask_for_photo=False))
        out = ps.analyze_photo(sample_photo())
        self.assertEqual(out["source"], "sample")
        self.assertIn("did not look at the photo", out["note"])

    def test_description_mode_needs_no_photo(self):
        fake = FakeClient(json.dumps(GOOD_REPLY), ask_for_photo=False)
        _patch(fake)
        out = ps.analyze_photo(None, [], "beige linen pants")
        self.assertEqual((out["source"], out["mode"]), ("zoowork_agent", "description"))
        self.assertEqual(fake.message["description"], "beige linen pants")

    def test_nothing_given_is_a_labelled_sample(self):
        out = ps.analyze_photo(None, [], "  ")
        self.assertEqual(out["source"], "sample")


class SheetTests(unittest.TestCase):
    def test_pins_spread_when_sharing_a_region(self):
        pos = pin_positions([{"region": "waist"}, {"region": "waist"}, {"region": "nope"}])
        self.assertEqual(pos, [(50, 38), (64, 38), (84, 50)])

    def test_sheet_has_photo_changes_and_target(self):
        spec = ps.parse_reply(json.dumps(GOOD_REPLY))
        econ = compute(Inputs(29.99, 9.0, 1.5, 0.12, 0.15, 5.4, 0.10))
        sheet = build_spec_sheet(sample_photo(), spec, spec["improvements"], econ)
        self.assertIn("data:image/jpeg;base64,", sheet)
        self.assertIn("add lining?", sheet)
        self.assertIn(f"${econ['landed']:.2f}", sheet)
        self.assertIn("OEKO-TEX Standard 100", sheet)
        self.assertNotIn("<script", sheet)


if __name__ == "__main__":
    unittest.main()
