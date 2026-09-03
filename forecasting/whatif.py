"""FinPilot's what-if cash simulator.

Answers one specific question: "if we collect on these overdue receivables,
how much runway does that buy back?" Reuses cashflow.project() twice --
once as the untouched baseline, once with the recovered amounts added as
scheduled inflows -- and reports the difference. Deterministic, same as
the forecaster and the receivables ranking; the only thing this module
adds is the comparison.
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from forecasting.cashflow import load_expenses, project  # noqa: E402
from receivables.ranking import load_receivables  # noqa: E402


def simulate_recovery(receivable_ids: list[str], data_dir: Path, opening_balance: float,
                       min_buffer: float, horizon: int, as_of: date, collection_lag_days: int = 7) -> dict:
    """collection_lag_days models the realistic gap between "customer agrees
    to pay" and "money lands in the account" -- a recovery push doesn't
    collect instantly."""
    expenses = load_expenses(data_dir / "expenses.csv")
    receivables = {r["receivable_id"]: r for r in load_receivables(data_dir / "receivables.csv")}

    missing = [rid for rid in receivable_ids if rid not in receivables]
    if missing:
        return {"error": f"unknown receivable id(s): {missing}"}

    recovered_total = sum(receivables[rid]["amount"] for rid in receivable_ids)
    collection_date = as_of + timedelta(days=collection_lag_days)
    inflows = [{"date": collection_date, "amount": recovered_total}]

    baseline_curve, baseline_shortfall = project(opening_balance, expenses, as_of, horizon, min_buffer)
    scenario_curve, scenario_shortfall = project(opening_balance, expenses, as_of, horizon, min_buffer, inflows)

    def shortfall_desc(day):
        if day is None:
            return None
        return {"days_out": day, "date": (as_of + timedelta(days=day)).isoformat()}

    runway_extension = None
    if baseline_shortfall is not None and scenario_shortfall is not None:
        runway_extension = scenario_shortfall - baseline_shortfall
    elif baseline_shortfall is not None and scenario_shortfall is None:
        runway_extension = f">{horizon - baseline_shortfall} (no shortfall within horizon)"

    return {
        "receivables_recovered": receivable_ids,
        "recovered_amount": round(recovered_total, 2),
        "assumed_collection_date": collection_date.isoformat(),
        "baseline_shortfall": shortfall_desc(baseline_shortfall),
        "scenario_shortfall": shortfall_desc(scenario_shortfall),
        "runway_extension_days": runway_extension,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent.parent / "data")
    parser.add_argument("--receivables", nargs="+", required=True, help="e.g. REC-3000 REC-3004")
    parser.add_argument("--opening-balance", type=float, default=1_500_000.0)
    parser.add_argument("--min-buffer", type=float, default=100_000.0)
    parser.add_argument("--horizon", type=int, default=90)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 8, 30))
    parser.add_argument("--collection-lag-days", type=int, default=7)
    args = parser.parse_args()

    result = simulate_recovery(
        args.receivables, args.data_dir, args.opening_balance, args.min_buffer,
        args.horizon, args.as_of, args.collection_lag_days,
    )
    if "error" in result:
        print(result["error"])
        return

    print(f"Recovering {result['receivables_recovered']} "
          f"(₹{result['recovered_amount']:,.0f}, assumed collected {result['assumed_collection_date']}):\n")
    print(f"  baseline shortfall:  {result['baseline_shortfall']}")
    print(f"  scenario shortfall:  {result['scenario_shortfall']}")
    print(f"  runway extension:    {result['runway_extension_days']} days")


if __name__ == "__main__":
    main()
