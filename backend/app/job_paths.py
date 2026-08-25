"""Job-folder path helpers, split out of store.py so job_audit.py,
checkpoints.py, and job_retention.py can resolve a job's directory without
importing store.py itself (store.py imports functions from all three of
those, so any of them importing store.py back would be circular)."""
import re
from pathlib import Path

JOBS_DIR = Path(__file__).resolve().parent.parent / "jobs"

# Every job_id ultimately comes from the URL path on every request (job_id
# is a plain str path parameter in every route), so without this check a
# crafted value like ".." could walk JOBS_DIR / job_id outside JOBS_DIR
# entirely wherever that's built (job_dir, store.job_exists, store.
# _ensure_loaded, store.delete_job). That's currently blocked incidentally
# by Starlette/uvicorn's own path-segment normalization rejecting ".."-like
# segments at the routing layer -- verified by testing it directly -- not by
# anything in this file. This regex is the app's own guarantee instead of
# silently depending on that framework behavior never changing. Matches
# store._next_job_id's own output format exactly (YYYYMMDD_NNN), so every
# legitimately-generated job_id always passes.
JOB_ID_RE = re.compile(r"^\d{8}_\d{3}$")


def ensure_jobs_dir():
    """JOBS_DIR is normally created once at import time (below), but if the
    whole folder gets deleted while the server process is still running,
    every function that scans it directly (store._next_job_id, store.
    list_job_summaries, job_retention._stored_job_records, job_retention.
    enforce_job_retention) needs to recreate it first or JOBS_DIR.iterdir()
    raises FileNotFoundError."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


ensure_jobs_dir()


def job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise ValueError(f"invalid job_id: {job_id!r}")
    d = JOBS_DIR / job_id
    # parents=True: JOBS_DIR itself is only created once, at import time
    # (above) -- if the whole jobs/ folder gets deleted while the server
    # process is still running, a plain mkdir(exist_ok=True) here would
    # raise FileNotFoundError since its parent no longer exists.
    d.mkdir(parents=True, exist_ok=True)
    return d
