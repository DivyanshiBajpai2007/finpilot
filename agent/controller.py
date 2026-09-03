"""FinPilot's Finance Controller agent.

A thin orchestration layer: every question is answered by calling the
deterministic tools in tools.py, never by the model inventing a number.
Every turn -- the question, every tool call and its result, and the final
answer -- is appended to data/audit_log.jsonl, so any recommendation can be
traced back to the exact data it came from and the model version that made
the call.

Uses the Gemini API's manual function-calling loop (not the SDK's automatic
mode) so every tool call can be individually logged to the audit trail.

Usage:
    python controller.py --ask "why did cash decrease this month?"
    python controller.py                 # interactive REPL
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from tools import TOOL_SPECS, call_tool

MODEL = "gemini-3.5-flash-lite"
AUDIT_LOG = Path(__file__).parent.parent / "data" / "audit_log.jsonl"

SYSTEM_PROMPT = """You are FinPilot, an autonomous finance controller for a small \
business, built for the Razorpay AI Buildathon's Finance Controller track. You have \
tools that query the business's actual reconciliation and cash-flow data -- always \
call a tool to get a number before stating it. Never estimate, round loosely, or \
state a financial figure that didn't come from a tool result.

This applies to tool INPUTS too, not just outputs: never invent a value for a \
parameter like opening_balance, min_buffer, or as_of. If the user hasn't told you \
that figure in this conversation, omit the parameter and let the tool use its own \
default, and say plainly in your answer that you're using the default rather than a \
real reported balance. Only pass a specific value if the user actually gave you one. \
The same applies to receivable_ids for simulate_recovery -- only use ids that came \
from a real list_receivables call, never a made-up REC-#### id.

simulate_recovery is read-only: it projects what collecting on a receivable would do, \
it does not collect anything or contact anyone. If asked to actually recover money or \
send a reminder, explain that you can only simulate and recommend -- the actual \
recovery workflow (finpilot/agent/recovery.py) requires a human to review and approve \
each message before anything is sent.

When you recommend an action, say what it costs and what it's expected to achieve, \
and make clear a human still has to approve it -- you are not authorized to execute \
anything yourself. Keep answers short and concrete."""


def log_audit(entry: dict):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "model": MODEL, **entry}
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def build_tools():
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=spec["name"], description=spec["description"], parameters=spec["input_schema"],
        )
        for spec in TOOL_SPECS
    ])]


def ask(client, question: str, history: list) -> str:
    contents = history + [types.Content(role="user", parts=[types.Part(text=question)])]
    tool_calls_this_turn = []
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=build_tools())

    while True:
        response = client.models.generate_content(model=MODEL, contents=contents, config=config)
        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = [p.function_call for p in candidate.content.parts if p.function_call]
        if not function_calls:
            final_text = "".join(p.text for p in candidate.content.parts if p.text)
            log_audit({"question": question, "tool_calls": tool_calls_this_turn, "answer": final_text})
            history[:] = contents
            return final_text

        response_parts = []
        for fc in function_calls:
            tool_input = dict(fc.args) if fc.args else {}
            result = call_tool(fc.name, tool_input)
            tool_calls_this_turn.append({"tool": fc.name, "input": tool_input, "result": result})
            response_parts.append(
                types.Part.from_function_response(name=fc.name, response={"result": result})
            )
        contents.append(types.Content(role="user", parts=response_parts))


def run_repl(client):
    history = []
    print("FinPilot -- ask a finance question ('exit' to quit)\n")
    while True:
        try:
            question = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in ("exit", "quit"):
            break
        answer = ask(client, question, history)
        print(f"\nFinPilot > {answer}\n")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ask", type=str, default=None, help="ask one question and exit")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if genai is None or not api_key:
        reason = "google-genai package not installed" if genai is None else "GEMINI_API_KEY not set"
        print(f"[agent] {reason} -- the agent needs a live key to run.")
        print("[agent] Add GEMINI_API_KEY to finpilot/.env and try again.")
        print("[agent] The tools it would call still work standalone -- see tools.py.")
        return

    client = genai.Client(api_key=api_key)
    if args.ask:
        print(ask(client, args.ask, []))
    else:
        run_repl(client)


if __name__ == "__main__":
    main()
