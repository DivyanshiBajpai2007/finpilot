"""Deterministic tool functions the FinPilot agent calls.

Every number the agent surfaces comes from one of these functions -- never
from the model itself. Each is a plain Python function over the CSVs in
data/, paired with an Anthropic tool-use schema in TOOL_SPECS so the agent
can call it by name. Nothing here talks to an LLM; that separation is the
whole point -- these are testable and correct on their own.
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from forecasting.cashflow import load_expenses, project  # noqa: E402
from forecasting.whatif import simulate_recovery as _simulate_recovery  # noqa: E402
from receivables.ranking import load_receivables, rank  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / "data"


def _load_csv(name: str) -> list[dict]:
    path = DATA_DIR / name
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def reconciliation_summary(**_) -> dict:
    """Overall match rate and exception breakdown across the full batch."""
    report = _load_csv("reconciliation_report.csv")
    if not report:
        return {"error": "no reconciliation_report.csv found -- run matcher.py first"}
    total = len(report)
    matched = [r for r in report if r["status"] == "matched"]
    exceptions = [r for r in report if r["status"] == "exception"]
    by_resolution, by_category = {}, {}
    for r in exceptions:
        by_resolution[r["resolution"]] = by_resolution.get(r["resolution"], 0) + 1
        by_category[r["category"]] = by_category.get(r["category"], 0) + 1
    return {
        "total_records": total,
        "matched": len(matched),
        "match_rate_pct": round(len(matched) / total * 100, 1) if total else 0,
        "exceptions": len(exceptions),
        "exceptions_by_resolution": by_resolution,
        "exceptions_by_category": by_category,
    }


CHECK_KEYS = ["order_matched", "payment_matched", "invoice_amount_matched",
              "settlement_amount_matched", "timing_matched"]


def _parse_check(v: str):
    return {"True": True, "False": False, "": None}.get(v, None)


def get_exception(order_ref: str, **_) -> dict:
    """Full detail -- category, delta, resolution, per-check breakdown,
    confidence, explanation -- for one order. The checks/confidence come
    straight from the deterministic matcher, not the LLM."""
    report = {r["order_ref"]: r for r in _load_csv("reconciliation_report.csv")}
    explanations = {r["order_ref"]: r for r in _load_csv("exception_explanations.csv")}
    row = report.get(order_ref)
    if row is None:
        return {"error": f"no record for {order_ref}"}
    checks = {k: _parse_check(row.get(k, "")) for k in CHECK_KEYS}
    if row["status"] != "exception":
        return {"order_ref": order_ref, "status": "matched", "checks": checks,
                 "confidence": row.get("confidence"),
                 "note": "This record matched cleanly -- no exception."}
    explanation = explanations.get(order_ref)
    return {
        "order_ref": order_ref,
        "category": row["category"],
        "resolution": row["resolution"],
        "delta": row["delta"],
        "confidence": row.get("confidence"),
        "checks": checks,
        "rule_based_note": row["note"],
        "explanation": explanation["explanation"] if explanation else row["note"],
        "llm_used": explanation["llm_used"] if explanation else "False",
        **_amounts_for(order_ref),
    }


def _amounts_for(order_ref: str) -> dict:
    """Raw expected-vs-settled figures for the exception detail view --
    pulled straight from payments.csv/bank.csv, not derived or guessed."""
    payments = {r["order_ref"]: r for r in _load_csv("payments.csv")}
    bank_rows = {}
    for r in _load_csv("bank.csv"):
        bank_rows.setdefault(r["batch_ref"], []).append(r)
    pay = payments.get(order_ref)
    if not pay:
        return {}
    expected_net = round(float(pay["amount"]) - float(pay["fee"]), 2)
    settlements = bank_rows.get(order_ref, [])
    settled = round(sum(float(b["credit"]) for b in settlements), 2) if settlements else None
    return {
        "payment_amount": pay["amount"],
        "fee": pay["fee"],
        "expected_net": expected_net,
        "settled_amount": settled,
        "difference": round(settled - expected_net, 2) if settled is not None else None,
    }


def list_exceptions(resolution: str = None, category: str = None, limit: int = 20, **_) -> dict:
    """List exceptions, optionally filtered by resolution or category."""
    rows = [r for r in _load_csv("reconciliation_report.csv") if r["status"] == "exception"]
    if resolution:
        rows = [r for r in rows if r["resolution"] == resolution]
    if category:
        rows = [r for r in rows if r["category"] == category]
    rows = rows[:limit]
    return {"count": len(rows), "exceptions": [
        {"order_ref": r["order_ref"], "category": r["category"],
         "resolution": r["resolution"], "delta": r["delta"], "note": r["note"]}
        for r in rows
    ]}


def cashflow_snapshot(opening_balance: float = 1_500_000.0, min_buffer: float = 100_000.0,
                       horizon: int = 90, as_of: str = "2026-08-30", **_) -> dict:
    """Project cash balance forward against known future expenses."""
    expenses_path = DATA_DIR / "expenses.csv"
    if not expenses_path.exists():
        return {"error": "no expenses.csv found -- run generate_dataset.py first"}
    expenses = load_expenses(expenses_path)
    as_of_date = date.fromisoformat(as_of)
    curve, shortfall_day = project(opening_balance, expenses, as_of_date, horizon, min_buffer)
    balance_by_day = dict(curve)

    result = {
        "opening_balance": opening_balance,
        "as_of": as_of,
        "min_buffer": min_buffer,
        "snapshots": {f"+{d}d": round(balance_by_day[d], 2)
                      for d in (7, 30, 60, 90) if d in balance_by_day},
    }
    if shortfall_day is not None:
        result["shortfall_in_days"] = shortfall_day
        result["shortfall_date"] = (as_of_date + timedelta(days=shortfall_day)).isoformat()
    else:
        result["shortfall_in_days"] = None
    return result


def list_receivables(limit: int = 5, as_of: str = "2026-08-30", **_) -> dict:
    """Top outstanding invoices ranked by expected recoverable value (amount x recovery likelihood)."""
    receivables_path = DATA_DIR / "receivables.csv"
    if not receivables_path.exists():
        return {"error": "no receivables.csv found -- run generate_dataset.py first"}
    ranked = rank(load_receivables(receivables_path), date.fromisoformat(as_of))
    return {"count": len(ranked), "top": ranked[:limit]}


def simulate_recovery(receivable_ids: list, opening_balance: float = 1_500_000.0,
                       min_buffer: float = 100_000.0, horizon: int = 90,
                       as_of: str = "2026-08-30", collection_lag_days: int = 7, **_) -> dict:
    """What-if: if these specific receivable_ids get collected, how much does
    the cash-shortfall date move? Use the ids returned by list_receivables."""
    return _simulate_recovery(
        list(receivable_ids), DATA_DIR, opening_balance, min_buffer, horizon,
        date.fromisoformat(as_of), collection_lag_days,
    )


TOOL_SPECS = [
    {
        "name": "reconciliation_summary",
        "description": "Get the overall reconciliation match rate and exception "
                        "breakdown across the full batch.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_exception",
        "description": "Get full detail -- category, delta, resolution, and "
                        "explanation -- for one specific order's reconciliation exception.",
        "input_schema": {
            "type": "object",
            "properties": {"order_ref": {"type": "string", "description": "e.g. ORD-2013"}},
            "required": ["order_ref"],
        },
    },
    {
        "name": "list_exceptions",
        "description": "List reconciliation exceptions, optionally filtered by "
                        "resolution (resolved_automatically / needs_review / "
                        "insufficient_evidence) or category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resolution": {"type": "string"},
                "category": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "cashflow_snapshot",
        "description": "Project cash balance forward from an opening balance against "
                        "known future expenses. Returns balance at +7/+30/+60/+90 days "
                        "and the date balance drops below the minimum buffer, if any.",
        "input_schema": {
            "type": "object",
            "properties": {
                "opening_balance": {"type": "number", "description": "cash in the bank as of as_of, in rupees"},
                "min_buffer": {"type": "number", "description": "minimum cash buffer to stay above, in rupees"},
                "horizon": {"type": "integer", "description": "days to project forward"},
                "as_of": {"type": "string", "description": "ISO date, e.g. 2026-08-30"},
            },
        },
    },
    {
        "name": "list_receivables",
        "description": "List outstanding customer invoices ranked by expected recoverable "
                        "value (amount x recovery likelihood, which decays with how overdue "
                        "the invoice is).",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "how many to return, default 5"},
                "as_of": {"type": "string", "description": "ISO date, e.g. 2026-08-30"},
            },
        },
    },
    {
        "name": "simulate_recovery",
        "description": "What-if: if these specific receivables (by receivable_id, from "
                        "list_receivables) get collected, how many days does the cash-shortfall "
                        "date move out by? Does not take any real action -- read-only simulation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "receivable_ids": {"type": "array", "items": {"type": "string"},
                                    "description": "e.g. [\"REC-3001\", \"REC-3019\"]"},
                "opening_balance": {"type": "number"},
                "min_buffer": {"type": "number"},
                "horizon": {"type": "integer"},
                "as_of": {"type": "string", "description": "ISO date, e.g. 2026-08-30"},
                "collection_lag_days": {"type": "integer",
                                         "description": "days between a recovery push and money landing, default 7"},
            },
            "required": ["receivable_ids"],
        },
    },
]

TOOL_FUNCTIONS = {
    "reconciliation_summary": reconciliation_summary,
    "get_exception": get_exception,
    "list_exceptions": list_exceptions,
    "cashflow_snapshot": cashflow_snapshot,
    "list_receivables": list_receivables,
    "simulate_recovery": simulate_recovery,
}


def call_tool(name: str, tool_input: dict) -> dict:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    return fn(**tool_input)
