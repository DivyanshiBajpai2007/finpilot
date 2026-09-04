"""Shared audit logging -- one append-only trail for every stage of the
pipeline (reconciliation runs, exception resolution, agent conversations,
recovery actions), not just the chat agent. This is what the "agent
activity" feed in the UI reads from; nothing in that feed is narrated,
every line is a real logged event.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG = Path(__file__).parent.parent / "data" / "audit_log.jsonl"


def log_audit(entry: dict, log_path: Path = None):
    path = log_path or AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_recent(limit: int = 20, log_path: Path = None) -> list[dict]:
    path = log_path or AUDIT_LOG
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]
    entries = [json.loads(line) for line in lines[-limit:]]
    return list(reversed(entries))
