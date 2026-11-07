"""MarginGuard HTTP API.

Wraps agent/run.py so a UI can start a run, follow its events, approve or reject each price
change and each supplier email draft, and read the plan, report, prices and outbox. One run at a
time.

Start:
  .venv/bin/uvicorn api.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agent.run import PRICE_CHANGES, SUPPLIER_EMAILS, MarginGuardRun, read_prices  # noqa: E402
from tools.supplier import read_outbox  # noqa: E402

# The platform denies an unanswered approval after 10 minutes; give up just before that.
APPROVAL_TIMEOUT_S = 9.5 * 60

CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "MARGINGUARD_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if o.strip()
]

app = FastAPI(title="MarginGuard API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
)


def error(http_status: int, code: str, **extra: Any) -> JSONResponse:
    """Errors use the starter app's shape: {"error": "<code>"}."""
    return JSONResponse({"error": code, **extra}, status_code=http_status)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error(400, "invalid_request", detail=exc.errors())


# ---------- run state ----------

class RunState:
    def __init__(self) -> None:
        self.run: MarginGuardRun | None = None
        self.task: asyncio.Task[None] | None = None
        self.pending: list[dict[str, Any]] | None = None
        self.pending_kind: str | None = None
        self.decision: asyncio.Future[dict[str, bool]] | None = None
        self.request: str | None = None

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    async def approver(self, kind: str, changes: list[dict[str, Any]]) -> dict[str, bool]:
        """Park the price changes or email drafts until POST /approve answers them."""
        self.pending = changes
        self.pending_kind = kind
        self.decision = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(self.decision, APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            if self.run:
                self.run.trace("approval.timeout", f"no decision; rejecting all {kind}")
            return {c["sku"]: False for c in changes}
        finally:
            self.pending = None
            self.pending_kind = None
            self.decision = None


state = RunState()


def require_run() -> MarginGuardRun | JSONResponse:
    return state.run if state.run is not None else error(404, "no_run")


# ---------- endpoints ----------

class RunBody(BaseModel):
    request: str = Field(min_length=1)
    reset: bool = False


class ApproveBody(BaseModel):
    decisions: dict[str, bool]


@app.post("/run", status_code=202)
async def start_run(body: RunBody) -> Any:
    if state.active:
        return error(409, "run_in_progress")
    state.run = MarginGuardRun(state.approver)
    state.request = body.request

    async def runner(run: MarginGuardRun) -> None:
        try:
            await run.run(body.request, reset=body.reset)
        except Exception:
            pass  # recorded on run.status / run.error

    state.task = asyncio.create_task(runner(state.run))
    return {"status": "running", "request": body.request, "reset": body.reset}


@app.get("/events")
async def events(since: int = 0) -> Any:
    run = require_run()
    if isinstance(run, JSONResponse):
        return run
    return {
        "status": run.status,
        "error": run.error,
        "session_id": run.session_id,
        "events": run.events[max(since, 0):],
        "next": len(run.events),
    }


@app.get("/pending")
async def pending() -> Any:
    items = state.pending or []
    return {
        "status": state.run.status if state.run else "idle",
        "pending": state.pending is not None,
        "kind": state.pending_kind,
        "changes": items if state.pending_kind == PRICE_CHANGES else [],
        "emails": items if state.pending_kind == SUPPLIER_EMAILS else [],
    }


@app.post("/approve")
async def approve(body: ApproveBody) -> Any:
    if state.pending is None or state.decision is None or state.decision.done():
        return error(409, "no_pending_approval")
    skus = {c["sku"] for c in state.pending}
    missing = sorted(skus - body.decisions.keys())
    unknown = sorted(body.decisions.keys() - skus)
    if missing or unknown:
        return error(400, "decisions_mismatch", missing=missing, unknown=unknown)
    decisions = {sku: body.decisions[sku] for sku in skus}
    state.decision.set_result(decisions)
    return {
        "approved": sorted(s for s, ok in decisions.items() if ok),
        "rejected": sorted(s for s, ok in decisions.items() if not ok),
    }


@app.get("/plan")
async def plan() -> Any:
    run = require_run()
    if isinstance(run, JSONResponse):
        return run
    p = run.plan
    return {
        "status": run.status,
        "decisions": p.get("decisions"),
        "reprice_prices": p.get("reprice_prices"),
        "comparables": p.get("comparables"),
        "baseline_totals": (p.get("baseline") or {}).get("totals"),
        "scenario_totals": (p.get("scenario") or {}).get("totals"),
        "scenario_input": (p.get("scenario") or {}).get("input"),
    }


@app.get("/report")
async def report() -> Any:
    run = require_run()
    if isinstance(run, JSONResponse):
        return run
    if run.status not in ("succeeded", "failed"):
        return error(409, "report_not_ready", status=run.status)
    applied = (run.apply_result or {}).get("applied", [])
    rejected = (run.apply_result or {}).get("rejected", [])
    return {
        "status": run.status,
        "error": run.error,
        "request": state.request,
        "report": run.report,
        "applied": applied,
        "rejected": rejected,
        "exposure": ((run.plan.get("baseline") or {}).get("totals") or {}).get("exposure"),
        "estimated_recovery": ((run.apply_result or {}).get("applied_totals") or {}).get("recovery"),
        "negotiation": run.negotiation,
        "timings": run.timings(),
    }


@app.get("/prices")
async def prices() -> Any:
    return read_prices()


@app.get("/outbox")
async def outbox() -> Any:
    """Approved supplier email drafts in data/outbox/. None of them were sent."""
    return {"sent": False, "emails": read_outbox()}
