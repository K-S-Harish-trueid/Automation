"""Job-folder capacity/retention enforcement, split out of store.py.

Scans JOBS_DIR directly (status.json files) rather than going through
store.py's in-memory _JOBS dict, since a job that was created in a previous
server run and never re-touched this run won't be loaded into memory at all
-- these need to see every job folder on disk regardless.

store.is_processing/store.delete_job are imported lazily (inside each
function, not at module load time) to avoid a circular import: store.py
imports these functions at module level, so if this module imported store.py
back at module level too, whichever one loads first would find the other
half-initialized."""
import json

from .job_paths import JOBS_DIR, ensure_jobs_dir
from .rules_config import MAX_STORED_JOBS

# Job folders contain KYC data, so the local workspace works as a rolling
# store. Once it fills up, the oldest non-processing job is removed for the
# new one. Set K2_MAX_STORED_JOBS (see rules_config.py) to adjust the
# default capacity -- just a same-file alias here, not its own env read.
MAX_DONE_JOBS = MAX_STORED_JOBS


def is_done(status: dict) -> bool:
    idx = status.get("stage_index", 0)
    stages = status.get("stages", [])
    if idx >= len(stages):
        return True
    stage = stages[idx]
    return stage.get("type") == "done" and stage.get("status") == "done"


def _stored_job_records() -> list[tuple[float, str]]:
    ensure_jobs_dir()
    records = []
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        status_file = job_dir / "status.json"
        if not status_file.exists():
            continue
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        created_at = status.get("created_at")
        if not isinstance(created_at, (int, float)):
            created_at = status_file.stat().st_ctime
        records.append((float(created_at), job_dir.name))
    return records


def enforce_job_capacity(
    max_stored_jobs: int = MAX_STORED_JOBS, *, protected_job_id: str | None = None
):
    """Keep a rolling number of job folders, evicting oldest eligible jobs first."""
    from . import store

    records = _stored_job_records()
    over_capacity = len(records) - max_stored_jobs
    if over_capacity <= 0:
        return

    for _, job_id in sorted(records):
        if over_capacity <= 0:
            break
        if job_id == protected_job_id or store.is_processing(job_id):
            continue
        store.delete_job(job_id)
        over_capacity -= 1


def enforce_job_retention(max_done_jobs: int = MAX_DONE_JOBS):
    """Keeps at most `max_done_jobs` completed jobs on disk (and in memory),
    deleting the oldest ones first. Jobs still waiting on a manual gate are
    never counted or touched, however many of them exist."""
    from . import store

    ensure_jobs_dir()
    done = []  # (mtime, job_id)
    for d in JOBS_DIR.iterdir():
        if not d.is_dir():
            continue
        status_file = d / "status.json"
        if not status_file.exists():
            continue
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if is_done(status):
            done.append((status_file.stat().st_mtime, d.name))

    done.sort(key=lambda t: t[0], reverse=True)  # newest first
    for _, job_id in done[max_done_jobs:]:
        store.delete_job(job_id)
