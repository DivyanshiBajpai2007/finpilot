"""FinPilot's exception resolver -- the LLM layer on top of the deterministic matcher.

matcher.py already decides the category, the delta, and the resolution for
every exception using pure rules. This module never touches any of that. Its
only job is to turn the grounded facts into a plain-English explanation a
business owner can read, and to flag any case where the facts don't actually
support the rule-based category. It cannot invent or change a number: every
fact in the prompt comes straight from the reconciliation report.

Uses the Gemini API. Falls back to the matcher's own rule-based note when no
GEMINI_API_KEY is set (or the call fails), so the rest of the pipeline still
runs end to end without a key.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load this project's own .env only -- a malformed/unrelated .env
    # elsewhere on the path (e.g. a different encoding) must never crash
    # the resolver; it should just mean no key was found.
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

try:
    from google import genai
except ImportError:
    genai = None

MODEL = "gemini-3.5-flash-lite"
BATCH_SIZE = 10

SYSTEM_PROMPT = """You are the exception-explanation layer inside FinPilot, an SMB \
finance controller agent. You are given a batch of already-classified reconciliation \
exceptions: a rule-based engine has already computed the category, the delta, and a \
resolution for each one. Your only job is to write a short, plain-English explanation \
of each exception for a small business owner, and to say whether the given facts \
actually support the rule-based category.

Rules:
- Never invent, adjust, or restate a different number than the ones given.
- If the facts don't clearly support the category, set "agrees" to false and say why in one line.
- Keep each explanation to one sentence, no jargon.
- Respond with a JSON array, one object per input item, in the same order, each \
shaped like: {"order_ref": "...", "explanation": "...", "agrees": true, "flag_reason": ""}"""


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_batches(items: list[dict], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def call_gemini(client, batch: list[dict]) -> list[dict]:
    payload = [
        {
            "order_ref": r["order_ref"],
            "category": r["category"],
            "resolution": r["resolution"],
            "delta": r["delta"],
            "rule_based_note": r["note"],
        }
        for r in batch
    ]
    response = client.models.generate_content(
        model=MODEL,
        contents=json.dumps(payload),
        config={
            "system_instruction": SYSTEM_PROMPT,
            "response_mime_type": "application/json",
        },
    )
    return json.loads(response.text)


def fallback_row(r: dict) -> dict:
    return {"order_ref": r["order_ref"], "llm_used": False,
            "explanation": r["note"], "agrees": True, "flag_reason": ""}


def resolve(data_dir: Path) -> list[dict]:
    report = load_csv(data_dir / "reconciliation_report.csv")
    exceptions = [r for r in report if r["status"] == "exception"]

    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key) if (genai and api_key) else None

    if client is None:
        reason = "google-genai package not installed" if genai is None else "GEMINI_API_KEY not set"
        print(f"  [exception resolver] {reason} -- falling back to rule-based notes only.\n")
        return [fallback_row(r) for r in exceptions]

    results = []
    for batch in build_batches(exceptions, BATCH_SIZE):
        try:
            parsed = call_gemini(client, batch)
            by_ref = {p["order_ref"]: p for p in parsed}
            for r in batch:
                p = by_ref.get(r["order_ref"])
                if p is None:
                    results.append(fallback_row(r))
                else:
                    results.append({
                        "order_ref": r["order_ref"], "llm_used": True,
                        "explanation": p.get("explanation", r["note"]),
                        "agrees": bool(p.get("agrees", True)),
                        "flag_reason": p.get("flag_reason", ""),
                    })
        except Exception as e:
            print(f"  [exception resolver] batch failed ({e}) -- falling back for these {len(batch)} rows.")
            results.extend(fallback_row(r) for r in batch)
    return results


def write_report(results: list[dict], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["order_ref", "llm_used", "explanation", "agrees", "flag_reason"])
        writer.writeheader()
        writer.writerows(results)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent.parent / "data")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out_path = args.out or (args.data_dir / "exception_explanations.csv")

    results = resolve(args.data_dir)
    write_report(results, out_path)

    llm_count = sum(1 for r in results if r["llm_used"])
    flagged = [r for r in results if not r["agrees"]]
    print(f"Exception resolver -- {len(results)} exceptions processed "
          f"({llm_count} via Gemini, {len(results) - llm_count} rule-based fallback)")
    if flagged:
        print(f"  {len(flagged)} flagged: the rule-based category didn't clearly match the facts")
        for r in flagged[:5]:
            print(f"    [{r['order_ref']}] {r['flag_reason']}")
    print(f"  full report -> {out_path}")


if __name__ == "__main__":
    main()
