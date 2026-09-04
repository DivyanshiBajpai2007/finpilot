"""FinPilot's bounded recovery workflow -- the one action in this system
that touches the outside world, and the only one gated behind explicit
human approval.

This script is deliberately separate from controller.py: the conversational
agent can rank receivables and simulate what recovering them would do, but
it cannot call this script itself. A human runs it, sees the exact draft
messages, and must type "y" before anything is recorded as sent. Nothing
here actually emails or texts a customer -- there's no real messaging
integration in this build -- but the approval gate and the audit trail are
real, and that's the part the architecture depends on.

Usage:
    python recovery.py --top 3
    python recovery.py --receivables REC-3001 REC-3019
"""
import argparse
import csv
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from receivables.ranking import load_receivables, rank  # noqa: E402
from agent.audit import log_audit  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / "data"
SENT_LOG = DATA_DIR / "sent_reminders.csv"


def draft_message(receivable: dict) -> str:
    return (
        f"Dear {receivable['customer']}, "
        f"our records show ₹{receivable['amount']:,.2f} outstanding "
        f"(due {receivable['due_date']}, {receivable['days_overdue']} days overdue). "
        f"Please arrange payment at your earliest convenience, or reply if you'd "
        f"like to discuss a payment plan."
    )


def append_sent_log(rows: list[dict]):
    is_new = not SENT_LOG.exists()
    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SENT_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["receivable_id", "customer", "amount", "message", "sent_at"])
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 8, 30))
    parser.add_argument("--top", type=int, default=None, help="draft for the top N ranked receivables")
    parser.add_argument("--receivables", nargs="+", default=None, help="or name specific receivable_ids")
    parser.add_argument("--yes", action="store_true",
                         help="skip the interactive prompt -- still logged, use for scripted demos only")
    args = parser.parse_args()

    ranked = rank(load_receivables(args.data_dir / "receivables.csv"), args.as_of)
    if args.receivables:
        by_id = {r["receivable_id"]: r for r in ranked}
        missing = [rid for rid in args.receivables if rid not in by_id]
        if missing:
            print(f"Unknown receivable id(s): {missing}")
            return
        selected = [by_id[rid] for rid in args.receivables]
    else:
        selected = ranked[: args.top or 3]

    if not selected:
        print("Nothing to draft.")
        return

    print(f"Drafted {len(selected)} recovery reminder(s):\n")
    for r in selected:
        print(f"  [{r['receivable_id']}] {r['customer']} -- ₹{r['amount']:,.2f}, "
              f"{r['days_overdue']}d overdue, recovery likelihood {r['recovery_likelihood']:.0%}")
        print(f"    \"{draft_message(r)}\"\n")

    log_audit({
        "type": "recovery", "action": "recovery_reminders_drafted",
        "receivables": [r["receivable_id"] for r in selected],
        "total_amount": round(sum(r["amount"] for r in selected), 2),
    })

    if args.yes:
        approved = True
        print("[--yes passed -- skipping interactive confirmation]")
    else:
        answer = input(f"Send these {len(selected)} reminder(s)? [y/N] ").strip().lower()
        approved = answer == "y"

    if not approved:
        print("\nNot sent. Nothing recorded as sent; drafting is still in the audit log.")
        log_audit({"type": "recovery", "action": "recovery_reminders_rejected",
                    "receivables": [r["receivable_id"] for r in selected]})
        return

    sent_at = datetime.now(timezone.utc).isoformat()
    append_sent_log([
        {"receivable_id": r["receivable_id"], "customer": r["customer"],
         "amount": r["amount"], "message": draft_message(r), "sent_at": sent_at}
        for r in selected
    ])
    log_audit({
        "type": "recovery", "action": "recovery_reminders_sent",
        "receivables": [r["receivable_id"] for r in selected],
        "total_amount": round(sum(r["amount"] for r in selected), 2),
        "note": "Simulated send -- no real messaging integration in this build. "
                "The approval gate and audit trail are real.",
    })
    print(f"\nRecorded as sent -> {SENT_LOG}")
    print("(No real message was transmitted -- this build has no messaging integration. "
          "The approval gate and audit log entry are real.)")


if __name__ == "__main__":
    main()
