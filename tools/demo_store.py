"""Save and replay one recorded run as plain JSON (no pickle, nothing executable).

save_run(state, rfq_context, rfq_result) writes market/cache/demo_run.json; load_run() reads it
back. Bytes (the product photo) are stored as base64 and tuples are preserved, because the pages
compare run keys as tuples. The file stays on this machine (market/cache/demo_run.json is ignored
by git) because it contains review text from other people.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

DEMO_FILE = Path(__file__).resolve().parent.parent / "market" / "cache" / "demo_run.json"


def _to_json(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return {"__bytes__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, tuple):
        return {"__tuple__": [_to_json(x) for x in obj]}
    if isinstance(obj, dict):
        return {str(k): _to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, set)):
        return [_to_json(x) for x in obj]
    return obj


def _from_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj) == {"__bytes__"}:
            return base64.b64decode(obj["__bytes__"])
        if set(obj) == {"__tuple__"}:
            return tuple(_from_json(x) for x in obj["__tuple__"])
        return {k: _from_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_json(x) for x in obj]
    return obj


def save_run(state: dict[str, Any], rfq_context: Any, rfq_result: Any, path: Path = DEMO_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "rfq_context": rfq_context, "rfq_result": rfq_result}
    path.write_text(json.dumps(_to_json(payload)), encoding="utf-8")


def load_run(path: Path = DEMO_FILE) -> dict[str, Any]:
    return _from_json(json.loads(path.read_text(encoding="utf-8")))
