"""Runs FinPilot's full non-interactive pipeline in order: generate the
synthetic dataset, reconcile it, resolve exceptions, forecast cash flow,
and rank receivables. This is the one command a reviewer needs to
reproduce every number in the build brief.

Two pieces are deliberately left out of this script and must be run
separately:
  - agent/controller.py -- a live conversation needs a specific question,
    not a scripted one.
  - agent/recovery.py -- requires a real human at the y/N prompt; running
    it non-interactively here would defeat the point of the approval gate.

Usage:
    python run_pipeline.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
STEPS = [
    ("1/5  Generating synthetic dataset",
     [sys.executable, "data/generate_dataset.py", "--records", "500"]),
    ("2/5  Running reconciliation engine",
     [sys.executable, "reconciliation/matcher.py"]),
    ("3/5  Resolving exceptions (Gemini if GEMINI_API_KEY is set, else rule-based fallback)",
     [sys.executable, "agent/exception_resolver.py"]),
    ("4/5  Forecasting cash flow",
     [sys.executable, "forecasting/cashflow.py"]),
    ("5/5  Ranking outstanding receivables",
     [sys.executable, "receivables/ranking.py"]),
]


def main():
    for label, cmd in STEPS:
        print(f"\n{'=' * 64}\n{label}\n{'=' * 64}")
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode != 0:
            print(f"\n[run_pipeline] step failed (exit {result.returncode}) -- stopping.")
            sys.exit(result.returncode)

    print(f"\n{'=' * 64}\nDone -- every output is in data/.\n\n"
          f"Two more things to try yourself:\n"
          f'  python agent/controller.py --ask "..."   (needs GEMINI_API_KEY)\n'
          f"  python agent/recovery.py --top 3          (interactive approval gate)\n"
          f"{'=' * 64}")


if __name__ == "__main__":
    main()
