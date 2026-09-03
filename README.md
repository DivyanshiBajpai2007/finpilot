# FinPilot

An autonomous finance-controller agent for small businesses — built for Razorpay's AI Buildathon 2026, **Track 04: AI Finance Controller**.

It reconciles a business's payments against its bank settlements and invoices, explains every exception it can't auto-resolve, forecasts cash flow, ranks overdue receivables, and lets a conversational agent answer finance questions and recommend recovery action — with a human required to approve anything that actually goes out.

> The official track brief: *"Build an agent that closes one finance-ops loop across a 50+ record batch of synthetic data, reporting its match rate and the exceptions it could not resolve."* Evaluation bar: *"throughput plus measured accuracy plus an honest exception list."* Everything below is built and measured against that bar directly — see [Results](#results).

## Quick start

```bash
pip install -r requirements.txt
```

Optional — for the live LLM exception explanations and the conversational agent, add a [Gemini API key](https://aistudio.google.com/apikey) to `.env`:

```
GEMINI_API_KEY=your-key-here
```

Without a key, everything still runs — the exception resolver and agent fall back to rule-based output instead of erroring.

Run the full deterministic + batch pipeline in one command:

```bash
python run_pipeline.py
```

This regenerates the synthetic dataset, reconciles it, resolves exceptions, forecasts cash flow, and ranks receivables — reproducing every number in this README. Two pieces are intentionally left out of the scripted run and need to be tried by hand:

```bash
python agent/controller.py --ask "What's our cash position over the next 90 days?"
python agent/recovery.py --top 3          # interactive y/N approval gate
```

## Architecture

```
   Razorpay payments   Bank statement   Invoices / ledger
          └────────────────┼────────────────┘
                            ↓
                 Reconciliation engine  (deterministic)
                            ↓
              Matched  │  Exceptions  │  Anomalies
                            ↓
        Exception resolver (Gemini)  +  Cash-flow forecaster (deterministic)
                            ↓
              Receivables ranking  +  What-if recovery simulator  (deterministic)
                            ↓
                  Finance Controller agent  (Gemini, tool-calling)
                            ↓
        Human approval  →  Recovery workflow  →  Audit trail
```

The reconciliation engine, cash-flow forecaster, receivables ranking, and what-if simulator are pure Python with no LLM involved — every number they produce is reproducible from a stated formula. The LLM (Gemini) only explains and orchestrates: it phrases exception explanations grounded in the deterministic facts and answers questions by calling tools, but it never computes a financial number itself. It also can't execute anything — `agent/recovery.py` is a separate script gated behind an explicit human `y/N` prompt.

## Repository layout

```
finpilot/
  data/                  synthetic dataset generator + every run's output CSVs
  reconciliation/        matcher.py — the core deterministic reconciliation engine
  agent/                 exception_resolver.py, controller.py (agent), recovery.py (bounded action), tools.py
  forecasting/           cashflow.py (forecaster), whatif.py (recovery simulator)
  receivables/           ranking.py — expected-value ranking of outstanding invoices
  run_pipeline.py        one-command reproduction of every non-interactive step
```

## Results

Real, measured output from `run_pipeline.py` against 500 synthetic orders — not cherry-picked, and the full exception list is written to `data/reconciliation_report.csv`.

| Metric | Value |
|---|---|
| Records processed | 500 |
| Matched | 431 (**86.2%** match rate) |
| Exceptions | 69 — 27 resolved automatically, 31 needing review, 11 insufficient evidence |
| Self-check vs. injected ground truth | 500/500 (100%) |
| Cash-flow shortfall (₹15L opening balance) | Projected below the ₹1L buffer on **2026-10-01**, +32 days out |
| Outstanding receivables | 20 invoices, ₹5.79L total, ₹4.35L expected recoverable |
| Recovery simulation (top 3 receivables) | +9 days runway (shortfall pushed from Oct 1 → Oct 10) |

Exception categories are never silently dropped — every one is written to the report with a category, a resolution (`resolved_automatically` / `needs_review` / `insufficient_evidence`), and a plain-English reason.

## Governance

- The reconciliation engine, forecaster, ranking, and simulator are deterministic — no LLM call can change a number they produce.
- The agent's system prompt explicitly forbids inventing tool *inputs* (like an assumed opening balance), not just outputs — this was a real bug caught in testing and fixed.
- `simulate_recovery` is read-only. The only action that touches the outside world (`agent/recovery.py`) requires a human to see the exact drafted message and type `y` before anything is recorded as sent.
- Every agent turn and every recovery action (drafted / approved / rejected / sent) is appended to `data/audit_log.jsonl`, traceable back to the exact tool calls and inputs behind it.

## Honest limitations

- No real messaging integration — `recovery.py`'s "sent" state is logged, not actually transmitted. The approval gate and audit trail are real; the delivery channel is not.
- No web front end yet — everything here is a runnable CLI/backend. A React dashboard is the natural next layer on top of these same modules.
- The synthetic dataset is generated with deliberately correlated noise (fees, settlement lag, duplicates, missing records), not independently randomized — but it is still synthetic, not real transaction data.
