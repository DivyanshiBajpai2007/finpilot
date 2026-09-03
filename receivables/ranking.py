"""FinPilot's receivables ranking.

Deterministic and transparent by design, same as the cash-flow forecaster:
every outstanding invoice gets a recovery-likelihood score from a stated
formula (older = less likely to collect), and is ranked by expected
recoverable value = amount x likelihood. No ML, no LLM -- a business owner
can check the formula against any row by hand.
"""
import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

# Linear decay: a fresh invoice (0 days overdue) starts near-certain to
# collect; by FULLY_STALE_DAYS it's floored at MIN_LIKELIHOOD rather than
# hitting zero, since even very overdue invoices sometimes get paid.
FULLY_STALE_DAYS = 90
MIN_LIKELIHOOD = 0.05


def days_overdue(due_date: date, as_of: date) -> int:
    return max(0, (as_of - due_date).days)


def recovery_likelihood(overdue_days: int) -> float:
    decay = min(overdue_days / FULLY_STALE_DAYS, 1.0)
    return round(max(MIN_LIKELIHOOD, 1.0 - decay), 3)


def tier(likelihood: float) -> str:
    if likelihood >= 0.66:
        return "high"
    if likelihood >= 0.33:
        return "medium"
    return "low"


def load_receivables(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["amount"] = float(r["amount"])
        r["due_date"] = date.fromisoformat(r["due_date"])
    return rows


def rank(receivables: list[dict], as_of: date) -> list[dict]:
    ranked = []
    for r in receivables:
        overdue = days_overdue(r["due_date"], as_of)
        likelihood = recovery_likelihood(overdue)
        ranked.append({
            "receivable_id": r["receivable_id"],
            "customer": r["customer"],
            "amount": r["amount"],
            "due_date": r["due_date"].isoformat(),
            "days_overdue": overdue,
            "recovery_likelihood": likelihood,
            "tier": tier(likelihood),
            "expected_value": round(r["amount"] * likelihood, 2),
        })
    ranked.sort(key=lambda r: r["expected_value"], reverse=True)
    return ranked


def write_report(ranked: list[dict], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "receivable_id", "customer", "amount", "due_date",
            "days_overdue", "recovery_likelihood", "tier", "expected_value",
        ])
        writer.writeheader()
        writer.writerows(ranked)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent.parent / "data")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 8, 30))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out_path = args.out or (args.data_dir / "receivables_ranked.csv")

    receivables = load_receivables(args.data_dir / "receivables.csv")
    ranked = rank(receivables, args.as_of)
    write_report(ranked, out_path)

    total_outstanding = sum(r["amount"] for r in ranked)
    total_expected = sum(r["expected_value"] for r in ranked)
    print(f"FinPilot receivables -- {len(ranked)} outstanding invoices as of {args.as_of}")
    print(f"  total outstanding:  ₹{total_outstanding:,.0f}")
    print(f"  expected recoverable (amount x likelihood): ₹{total_expected:,.0f}\n")
    print(f"  {'id':<10} {'customer':<20} {'amount':>12} {'overdue':>8} {'tier':<8} {'expected':>12}")
    for r in ranked[:10]:
        print(f"  {r['receivable_id']:<10} {r['customer']:<20} "
              f"₹{r['amount']:>10,.0f} {r['days_overdue']:>6}d  {r['tier']:<8} ₹{r['expected_value']:>10,.0f}")
    print(f"\n  full ranking -> {out_path}")


if __name__ == "__main__":
    main()
