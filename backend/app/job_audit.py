"""Append-only per-job audit log (audit.jsonl), split out of store.py.

A large job can generate hundreds of thousands of field-level audit events
(every changed cell on every row, across every stage). Storing that array
inside status.json meant every store.persist() call -- dozens of times per
run -- rewrote the *entire* growing log from scratch, and every dashboard
listing (store.list_job_summaries, which runs on every /api/jobs page load)
had to parse right through it just to read a handful of small fields.
Keeping audit in its own append-only file fixes both: new events are
appended, not rewritten, and status.json stays small and fast regardless of
audit size.

This log is never truncated, including on rollback -- a rolled-back
checkpoint's audit entries stay on record rather than being silently
discarded, so the file is a complete historical record, not a mirror of
"what's true about the current data right now." That's a deliberate
tradeoff for simplicity/safety over the alternative (checkpoints owning a
truncation cursor into a shared file), which is real but sharper -- get a
truncation point wrong and you've destroyed audit history, not just kept
some extra. Nothing reads this file expecting it to only reflect
never-undone changes today, so this doesn't change any current behavior."""
import json
from pathlib import Path

from .job_paths import job_dir


def _audit_path(job_id: str) -> Path:
    return job_dir(job_id) / "audit.jsonl"


def append_audit_events(job_id: str, events: list[dict]) -> None:
    if not events:
        return
    with _audit_path(job_id).open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, separators=(",", ":")))
            f.write("\n")


def read_audit_events(job_id: str) -> list[dict]:
    path = _audit_path(job_id)
    if not path.exists():
        return []
    events = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def count_audit_events(job_id: str) -> int:
    """Line count without parsing each one -- cheap even at hundreds of
    thousands of events, for callers that only need the number."""
    path = _audit_path(job_id)
    if not path.exists():
        return 0
    with path.open("rb") as f:
        return sum(1 for _ in f)
