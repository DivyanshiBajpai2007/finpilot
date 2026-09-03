"""Generate FinPilot's synthetic reconciliation dataset.

Produces three sources that a real business would actually have —
Razorpay payments, a bank statement, and an invoice ledger — plus a
ground_truth.csv that records which noise type (if any) was injected
into each order. The ground truth isn't read by the matcher; it exists
so matcher.py's classifications can be checked against something real
instead of eyeballed.

Noise is injected deliberately, not via independent randomness per
field, so the three sources disagree the way real systems do: a fee
shaves the settlement, a bank credit lands a day late, a settlement
occasionally posts twice, an invoice sometimes lags the payment.
"""
import argparse
import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

FEE_RATE = 0.0236  # Razorpay's typical blended fee: ~2% + 18% GST on the fee

CUSTOMERS = [
    "Aarav Textiles", "Bansal Traders", "Chetak Logistics", "Devika Foods",
    "Everest Hardware", "Falcon Electricals", "Ganga Stationers", "Harit Agro",
    "Indus Apparel", "Jyoti Enterprises", "Kavya Interiors", "Lotus Bakery",
    "Meera Motors", "Nakshatra Gems", "Omkar Furnishings", "Prakash Paints",
    "Quantum Solutions", "Ravi Traders", "Shanti Sweets", "Tarun Auto Parts",
]

AMOUNT_BANDS = [
    ((500, 5000), 35),
    ((5000, 25000), 35),
    ((25000, 100000), 22),
    ((100000, 300000), 8),
]

NOISE_WEIGHTS = [
    ("clean", 84),
    ("fee_adjustment", 5),
    ("delayed_settlement", 3),
    ("duplicate_settlement", 2),
    ("missing_bank", 2),
    ("missing_invoice", 2),
    ("rounding", 1),
    ("amount_anomaly", 1),
]


def random_amount(rng: random.Random) -> float:
    band = rng.choices(
        [b for b, _ in AMOUNT_BANDS], weights=[w for _, w in AMOUNT_BANDS]
    )[0]
    return round(rng.uniform(*band), 2)


@dataclass
class GeneratedOrder:
    order_ref: str
    customer: str
    amount: float
    invoice_amount: float
    invoice_date: date
    invoice_present: bool
    payment_id: str
    fee: float
    settled_at: date
    bank_rows: list  # list of (bank_txn_id, credit, value_date)
    noise: str


def build_orders(n: int, rng: random.Random, start_date: date) -> list[GeneratedOrder]:
    noise_types = [t for t, _ in NOISE_WEIGHTS]
    noise_w = [w for _, w in NOISE_WEIGHTS]

    orders = []
    for i in range(n):
        order_ref = f"ORD-{2000 + i}"
        customer = rng.choice(CUSTOMERS)
        amount = random_amount(rng)
        invoice_date = start_date + timedelta(days=rng.randint(0, 89))
        fee = round(amount * FEE_RATE, 2)
        settled_at = invoice_date + timedelta(days=rng.randint(0, 2))
        payment_id = f"pay_{rng.randrange(10**8, 10**9):x}"
        bank_txn_id = f"BNK-{rng.randrange(10000, 99999)}"

        noise = rng.choices(noise_types, weights=noise_w)[0]

        invoice_amount = amount
        invoice_present = True
        bank_rows = [(bank_txn_id, round(amount - fee, 2), settled_at + timedelta(days=1))]

        if noise == "fee_adjustment":
            extra_deduction = round(rng.uniform(50, 2500), 2)
            credit = round(amount - fee - extra_deduction, 2)
            bank_rows = [(bank_txn_id, credit, settled_at + timedelta(days=1))]
        elif noise == "delayed_settlement":
            lag = rng.randint(2, 6)
            bank_rows = [(bank_txn_id, round(amount - fee, 2), settled_at + timedelta(days=lag))]
        elif noise == "duplicate_settlement":
            credit = round(amount - fee, 2)
            value_date = settled_at + timedelta(days=1)
            bank_rows = [
                (bank_txn_id, credit, value_date),
                (f"BNK-{rng.randrange(10000, 99999)}", credit, value_date + timedelta(days=1)),
            ]
        elif noise == "missing_bank":
            bank_rows = []
        elif noise == "missing_invoice":
            invoice_present = False
        elif noise == "rounding":
            drift = round(rng.uniform(-1.99, 1.99), 2)
            bank_rows = [(bank_txn_id, round(amount - fee + drift, 2), settled_at + timedelta(days=1))]
        elif noise == "amount_anomaly":
            credit = round(amount * rng.choice([0.15, 2.4, 0.05]), 2)
            bank_rows = [(bank_txn_id, credit, settled_at + timedelta(days=1))]

        orders.append(GeneratedOrder(
            order_ref=order_ref, customer=customer, amount=amount,
            invoice_amount=invoice_amount, invoice_date=invoice_date,
            invoice_present=invoice_present, payment_id=payment_id, fee=fee,
            settled_at=settled_at, bank_rows=bank_rows, noise=noise,
        ))
    return orders


def month_starts(start: date, horizon_days: int) -> list[date]:
    months = []
    cur = date(start.year, start.month, 1)
    end = start + timedelta(days=horizon_days)
    while cur <= end:
        months.append(cur)
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return months


def build_expenses(rng: random.Random, start_date: date, horizon_days: int = 180) -> list[dict]:
    """Recurring/committed outflows -- payroll, rent, utilities, vendor
    payments -- spanning start_date .. start_date + horizon_days. This is
    what the cash-flow forecaster projects against; it's deliberately wider
    than the payments window so the forecast has real future line items."""
    rows = []
    exp_id = 0

    def add(category: str, amount: float, due_date: date):
        nonlocal exp_id
        exp_id += 1
        rows.append({
            "expense_id": f"EXP-{exp_id:04d}",
            "category": category,
            "amount": f"{amount:.2f}",
            "due_date": due_date.isoformat(),
        })

    for month_start in month_starts(start_date, horizon_days):
        add("payroll", rng.uniform(380000, 480000), month_start.replace(day=1))
        add("rent", rng.uniform(75000, 95000), month_start.replace(day=5))
        add("utilities", rng.uniform(18000, 32000), month_start.replace(day=10))
        for _ in range(rng.randint(2, 4)):
            add("vendor_payment", rng.uniform(80000, 260000), month_start.replace(day=rng.randint(1, 27)))
    return rows


def build_receivables(rng: random.Random, as_of: date, count: int = 20) -> list[dict]:
    """Genuinely outstanding invoices -- not part of the Razorpay payment flow
    reconciled elsewhere. These are what receivables ranking and the recovery
    workflow operate on: real unpaid amounts, overdue by a real number of
    days as of `as_of`."""
    overdue_bands = [((1, 15), 40), ((15, 45), 35), ((45, 90), 25)]
    rows = []
    for i in range(count):
        band = rng.choices([b for b, _ in overdue_bands], weights=[w for _, w in overdue_bands])[0]
        days_overdue = rng.randint(*band)
        rows.append({
            "receivable_id": f"REC-{3000 + i}",
            "customer": rng.choice(CUSTOMERS),
            "amount": f"{random_amount(rng):.2f}",
            "due_date": (as_of - timedelta(days=days_overdue)).isoformat(),
            "status": "outstanding",
        })
    return rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    orders = build_orders(args.records, rng, date(2026, 6, 1))

    payments, bank, invoices, ground_truth = [], [], [], []
    for o in orders:
        payments.append({
            "payment_id": o.payment_id,
            "order_ref": o.order_ref,
            "amount": f"{o.amount:.2f}",
            "fee": f"{o.fee:.2f}",
            "settled_at": o.settled_at.isoformat(),
        })
        for txn_id, credit, value_date in o.bank_rows:
            bank.append({
                "txn_id": txn_id,
                "batch_ref": o.order_ref,
                "credit": f"{credit:.2f}",
                "value_date": value_date.isoformat(),
                "narration": "RAZORPAY SETL",
            })
        if o.invoice_present:
            invoices.append({
                "invoice_id": f"INV-{o.order_ref.split('-')[1]}",
                "order_ref": o.order_ref,
                "amount": f"{o.invoice_amount:.2f}",
                "customer": o.customer,
                "due_date": o.invoice_date.isoformat(),
                "status": "paid",
            })
        ground_truth.append({"order_ref": o.order_ref, "noise": o.noise})

    write_csv(args.out / "payments.csv",
              ["payment_id", "order_ref", "amount", "fee", "settled_at"], payments)
    write_csv(args.out / "bank.csv",
              ["txn_id", "batch_ref", "credit", "value_date", "narration"], bank)
    write_csv(args.out / "invoices.csv",
              ["invoice_id", "order_ref", "amount", "customer", "due_date", "status"], invoices)
    write_csv(args.out / "ground_truth.csv", ["order_ref", "noise"], ground_truth)

    expenses = build_expenses(rng, date(2026, 6, 1))
    write_csv(args.out / "expenses.csv", ["expense_id", "category", "amount", "due_date"], expenses)
    print(f"Generated {len(expenses)} expense line items -> {args.out / 'expenses.csv'}")

    receivables = build_receivables(rng, date(2026, 8, 30))
    write_csv(args.out / "receivables.csv",
              ["receivable_id", "customer", "amount", "due_date", "status"], receivables)
    print(f"Generated {len(receivables)} outstanding receivables -> {args.out / 'receivables.csv'}")

    counts = {}
    for o in orders:
        counts[o.noise] = counts.get(o.noise, 0) + 1
    print(f"Generated {len(orders)} orders -> {args.out}")
    for noise_type, _ in NOISE_WEIGHTS:
        print(f"  {noise_type:<20} {counts.get(noise_type, 0)}")


if __name__ == "__main__":
    main()
