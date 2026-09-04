"""FinPilot's web app -- a thin FastAPI layer over the same deterministic
tools and agent already used by the CLI. No new business logic lives here:
every number and every conversation goes through agent/tools.py,
agent/controller.py, and agent/recovery.py exactly as it does on the
command line. This file only adds HTTP routing, a static dashboard, and a
rate limit on the one endpoint that spends money.
"""
import os
import sys
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

import tools  # agent/tools.py
import recovery  # agent/recovery.py

try:
    from google import genai
except ImportError:
    genai = None

app = FastAPI(title="FinPilot API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_gemini_client = None
if genai and os.environ.get("GEMINI_API_KEY"):
    _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# ---- rate limiter, scoped to /api/ask only -- that's the sole endpoint
# that spends real API credit. Everything else reads cached CSVs. ----
ASK_LIMIT = 8
ASK_WINDOW_SECONDS = 60
_ask_calls: dict[str, deque] = defaultdict(deque)


def _rate_limited(client_ip: str) -> bool:
    now = time.time()
    q = _ask_calls[client_ip]
    while q and now - q[0] > ASK_WINDOW_SECONDS:
        q.popleft()
    if len(q) >= ASK_LIMIT:
        return True
    q.append(now)
    return False


class AskRequest(BaseModel):
    question: str


class SimulateRequest(BaseModel):
    receivable_ids: list[str]
    collection_lag_days: int = 7


class RecoveryDraftRequest(BaseModel):
    receivable_ids: list[str]


class RecoverySendRequest(BaseModel):
    receivable_ids: list[str]
    confirm: bool = False


@app.get("/api/summary")
def summary():
    return tools.reconciliation_summary()


@app.get("/api/exceptions")
def exceptions(resolution: Optional[str] = None, category: Optional[str] = None, limit: int = 20):
    return tools.list_exceptions(resolution=resolution, category=category, limit=limit)


@app.get("/api/exceptions/{order_ref}")
def exception_detail(order_ref: str):
    result = tools.get_exception(order_ref)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/api/cashflow")
def cashflow(opening_balance: float = 1_500_000.0, min_buffer: float = 100_000.0, horizon: int = 90):
    return tools.cashflow_snapshot(opening_balance=opening_balance, min_buffer=min_buffer, horizon=horizon)


@app.get("/api/cashflow/curve")
def cashflow_curve(opening_balance: float = 1_500_000.0, min_buffer: float = 100_000.0, horizon: int = 90):
    """Dashboard-only detail endpoint -- the full daily balance curve, not
    just the four snapshot points cashflow_snapshot gives the agent. Reuses
    the same deterministic project() function, just asks for more of it."""
    from forecasting.cashflow import load_expenses, project
    expenses = load_expenses(ROOT / "data" / "expenses.csv")
    as_of = date(2026, 8, 30)
    curve, shortfall_day = project(opening_balance, expenses, as_of, horizon, min_buffer)
    return {
        "as_of": as_of.isoformat(),
        "min_buffer": min_buffer,
        "shortfall_day": shortfall_day,
        "points": [
            {"day": d, "date": (as_of + timedelta(days=d)).isoformat(), "balance": round(b, 2)}
            for d, b in curve
        ],
    }


@app.get("/api/receivables")
def receivables(limit: int = 10):
    return tools.list_receivables(limit=limit)


@app.post("/api/simulate-recovery")
def simulate(req: SimulateRequest):
    result = tools.simulate_recovery(
        receivable_ids=req.receivable_ids, collection_lag_days=req.collection_lag_days,
    )
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


def _ranked_by_id() -> dict:
    return {r["receivable_id"]: r for r in tools.list_receivables(limit=1000)["top"]}


@app.post("/api/recovery/draft")
def recovery_draft(req: RecoveryDraftRequest):
    """Read-only: drafts messages for review. Nothing is sent from here."""
    ranked = _ranked_by_id()
    missing = [rid for rid in req.receivable_ids if rid not in ranked]
    if missing:
        raise HTTPException(404, f"unknown receivable id(s): {missing}")
    drafts = [
        {"receivable_id": rid, "customer": ranked[rid]["customer"], "amount": ranked[rid]["amount"],
         "days_overdue": ranked[rid]["days_overdue"], "message": recovery.draft_message(ranked[rid])}
        for rid in req.receivable_ids
    ]
    recovery.log_audit({
        "action": "recovery_reminders_drafted", "source": "web",
        "receivables": req.receivable_ids,
        "total_amount": round(sum(d["amount"] for d in drafts), 2),
    })
    return {"drafts": drafts}


@app.post("/api/recovery/send")
def recovery_send(req: RecoverySendRequest):
    """The one endpoint that records a real side effect -- gated behind an
    explicit confirm flag the frontend must set from its own confirmation
    step, not a default. Still just a simulated send: logged, not
    transmitted -- same honest behavior as the CLI version."""
    if not req.confirm:
        raise HTTPException(400, "confirm must be true -- this requires explicit human approval")
    ranked = _ranked_by_id()
    missing = [rid for rid in req.receivable_ids if rid not in ranked]
    if missing:
        raise HTTPException(404, f"unknown receivable id(s): {missing}")
    selected = [ranked[rid] for rid in req.receivable_ids]

    sent_at = datetime.now(timezone.utc).isoformat()
    recovery.append_sent_log([
        {"receivable_id": r["receivable_id"], "customer": r["customer"],
         "amount": r["amount"], "message": recovery.draft_message(r), "sent_at": sent_at}
        for r in selected
    ])
    recovery.log_audit({
        "action": "recovery_reminders_sent", "source": "web",
        "receivables": req.receivable_ids,
        "total_amount": round(sum(r["amount"] for r in selected), 2),
        "note": "Simulated send -- no real messaging integration in this build. "
                "The approval gate and audit trail are real.",
    })
    return {
        "sent": [r["receivable_id"] for r in selected],
        "note": "No real message was transmitted -- this build has no messaging "
                "integration. The approval gate and audit log entry are real.",
    }


@app.post("/api/ask")
def ask(req: AskRequest, request: Request):
    if _gemini_client is None:
        raise HTTPException(503, "GEMINI_API_KEY not configured on the server -- "
                                  "the agent needs a live key to answer questions.")
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        raise HTTPException(429, f"Rate limit: max {ASK_LIMIT} questions per {ASK_WINDOW_SECONDS}s. Try again shortly.")

    import controller  # imported lazily so a missing key never breaks the other routes
    result = controller.ask(_gemini_client, req.question, [])
    return {
        "answer": result["answer"],
        "tool_calls": [t["tool"] for t in result["tool_calls"]],
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "gemini_configured": _gemini_client is not None}


app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
