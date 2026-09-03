"""FinPilot's cash-flow forecaster.

Deterministic and LLM-free by design: projects cash forward from a stated
opening balance against known, dated future expenses (payroll, rent,
utilities, vendor payments). It answers "if nothing else changes, when do
we run tight" -- it deliberately does not assume any new revenue lands,
since collecting on outstanding receivables is a recovery *action* the
business can take (a what-if intervention), not a forecasting assumption.
That keeps the baseline honest: it's the runway you have today, not the
runway you're hoping for.
"""
import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

SNAPSHOT_DAYS = [7, 30, 60, 90]


def load_expenses(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["amount"] = float(r["amount"])
        r["due_date"] = date.fromisoformat(r["due_date"])
    return rows


def project(opening_balance: float, expenses: list[dict], as_of: date,
            horizon: int, min_buffer: float, inflows: list[dict] = None):
    """inflows, if given, is a list of {"date": date, "amount": float} --
    scheduled money coming IN (e.g. a receivable being collected), on top of
    the known expenses going out. Optional and keyword-compatible so every
    existing caller (the plain forecast, the cashflow_snapshot tool) is
    unaffected."""
    due_by_offset = {}
    for e in expenses:
        offset = (e["due_date"] - as_of).days
        if 0 <= offset <= horizon:
            due_by_offset[offset] = due_by_offset.get(offset, 0.0) + e["amount"]

    in_by_offset = {}
    for inflow in (inflows or []):
        offset = (inflow["date"] - as_of).days
        if 0 <= offset <= horizon:
            in_by_offset[offset] = in_by_offset.get(offset, 0.0) + inflow["amount"]

    balance = opening_balance
    curve = []
    shortfall_day = None
    for day in range(horizon + 1):
        balance += in_by_offset.get(day, 0.0) - due_by_offset.get(day, 0.0)
        curve.append((day, balance))
        if shortfall_day is None and balance < min_buffer:
            shortfall_day = day
    return curve, shortfall_day


def write_curve(curve: list[tuple], as_of: date, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "days_out", "projected_balance"])
        for day, balance in curve:
            writer.writerow([(as_of + timedelta(days=day)).isoformat(), day, f"{balance:.2f}"])


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent.parent / "data")
    parser.add_argument("--opening-balance", type=float, default=1_500_000.0,
                         help="cash in the bank as of --as-of")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 8, 30))
    parser.add_argument("--horizon", type=int, default=90)
    parser.add_argument("--min-buffer", type=float, default=100_000.0,
                         help="minimum cash buffer the business wants to stay above")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out_path = args.out or (args.data_dir / "cashflow_forecast.csv")

    expenses = load_expenses(args.data_dir / "expenses.csv")
    curve, shortfall_day = project(
        args.opening_balance, expenses, args.as_of, args.horizon, args.min_buffer
    )
    write_curve(curve, args.as_of, out_path)

    print(f"FinPilot cash-flow forecast -- opening balance ₹{args.opening_balance:,.0f} as of {args.as_of}")
    balance_by_day = dict(curve)
    for d in SNAPSHOT_DAYS:
        if d in balance_by_day:
            print(f"  +{d:>2} days   ₹{balance_by_day[d]:>13,.0f}")

    if shortfall_day is not None:
        shortfall_date = args.as_of + timedelta(days=shortfall_day)
        print(f"\n  shortfall risk: projected balance drops below the "
              f"₹{args.min_buffer:,.0f} buffer on {shortfall_date} (+{shortfall_day} days)")
    else:
        print(f"\n  no shortfall projected within {args.horizon} days against known "
              f"expenses (this assumes no new revenue is collected in that window).")
    print(f"\n  full curve -> {out_path}")


if __name__ == "__main__":
    main()
