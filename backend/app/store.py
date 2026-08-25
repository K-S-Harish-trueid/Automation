"""In-memory job store with a disk-backed snapshot for crash recovery /
download.

Owns the actual per-job state (_JOBS, _PROGRESS, _PROCESSING_JOBS) and the
core read/write operations on it. Everything else that used to live in this
file has been split out into its own module, each re-exported here so every
existing `store.xxx(...)` call site keeps working unchanged:
  job_paths.py     -- JOBS_DIR, job_id validation, job directory resolution
  job_audit.py      -- append-only audit.jsonl (append/read/count events)
  checkpoints.py    -- checkpoint create/list/rollback
  job_retention.py  -- capacity/retention eviction of old job folders
See each module's own docstring for why it's separate."""
import io
import json
import shutil
import threading
import time
from datetime import datetime

import pandas as pd
import pyarrow.parquet as pq

from .job_audit import append_audit_events, count_audit_events, read_audit_events  # noqa: F401
from .job_paths import JOBS_DIR, ensure_jobs_dir as _ensure_jobs_dir, job_dir as _job_dir
from .job_retention import (  # noqa: F401
    MAX_DONE_JOBS,
    enforce_job_capacity,
    enforce_job_retention,
    is_done as _is_done,
)
from .checkpoints import (  # noqa: F401
    create_checkpoint,
    get_rollback_targets,
    rollback_latest_checkpoint,
    rollback_to_checkpoint,
)

_JOBS: dict[str, dict] = {}

# Background-processing tracking, separate from the persisted job state:
# _PROGRESS holds the payload GET /progress reports; _PROCESSING_JOBS is the
# atomic "is a background task already running for this job_id" guard used
# for the 409 duplicate-request check.
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()
_PROCESSING_JOBS: set[str] = set()
_PROCESSING_LOCK = threading.Lock()

# Job IDs are "yyyymmdd_sno" -- the date plus a serial number reset each day
# (e.g. 20260727_001), so folder names sort chronologically for free.
_ID_LOCK = threading.Lock()


def _next_job_id() -> str:
    today = datetime.now().strftime("%Y%m%d")
    prefix = today + "_"
    _ensure_jobs_dir()
    with _ID_LOCK:
        used = {d.name for d in JOBS_DIR.iterdir() if d.is_dir() and d.name.startswith(prefix)}
        used |= {j for j in _JOBS if j.startswith(prefix)}
        serials = [int(name[len(prefix):]) for name in used if name[len(prefix):].isdigit()]
        next_serial = (max(serials) + 1) if serials else 1
        return f"{prefix}{next_serial:03d}"


def try_begin_processing(job_id: str) -> bool:
    """Atomically marks job_id as processing. Returns False if it already was."""
    with _PROCESSING_LOCK:
        if job_id in _PROCESSING_JOBS:
            return False
        _PROCESSING_JOBS.add(job_id)
        return True


def end_processing(job_id: str):
    with _PROCESSING_LOCK:
        _PROCESSING_JOBS.discard(job_id)


def set_progress(job_id: str, **fields):
    with _PROGRESS_LOCK:
        p = _PROGRESS.setdefault(job_id, {})
        if fields.get("status") != "error":
            p.pop("message", None)
        p.update(fields)


def get_progress(job_id: str) -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS.get(job_id, {"status": "idle"}))


def read_table(raw: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    if name.endswith(".csv"):
        last_err = None
        for enc in ["utf-8", "utf-8-sig", "cp1256", "cp1252", "latin1"]:
            try:
                df = pd.read_csv(
                    io.BytesIO(raw), dtype=str, keep_default_na=False, na_filter=False, encoding=enc
                )
                break
            except UnicodeDecodeError as e:
                last_err = e
                df = None
            except Exception as e:
                raise ValueError(f"Could not read CSV file {filename}: {e}") from e
        if df is None:
            raise ValueError(f"Could not decode {filename}: {last_err}")
    elif name.endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(io.BytesIO(raw), dtype=str, engine="calamine").fillna("")
        except Exception as e:
            raise ValueError(f"Could not read Excel file {filename}: {e}") from e
    else:
        raise ValueError("Unsupported file type. Upload a .csv, .xlsx, or .xls file.")

    df.columns = df.columns.astype(str).str.replace("﻿", "", regex=False).str.strip()
    return df


def preview_raw_upload(raw: bytes, filename: str) -> dict:
    """Read a file without creating a job so the upload screen can catch mistakes early."""
    df = read_table(raw, filename)
    from .pipeline import RAW_REQUIRED_COLS

    missing = [column for column in RAW_REQUIRED_COLS if column not in df.columns]
    duplicates = 0
    if "ACCOUNT_NUMBER" in df.columns:
        account_numbers = df["ACCOUNT_NUMBER"].astype(str).str.strip()
        duplicates = int(account_numbers.duplicated(keep=False).sum())
    return {
        "filename": filename,
        "file_size": len(raw),
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "columns": df.columns.astype(str).tolist(),
        "required_columns": RAW_REQUIRED_COLS,
        "missing_required_columns": missing,
        "duplicate_account_rows": duplicates,
    }


def create_job(raw: bytes, filename: str) -> str:
    job_id = _next_job_id()
    df = read_table(raw, filename)
    from .pipeline import RAW_REQUIRED_COLS, STAGES

    missing = [c for c in RAW_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Uploaded file missing required columns: {missing}")
    df = df.reset_index(drop=True)

    status = {
        "job_id": job_id,
        "filename": filename,
        "created_at": time.time(),
        "stage_index": 0,
        "stages": [{**s, "status": "pending"} for s in STAGES],
        "history": [],
        "drafts": {},
        "checkpoints": [],
    }
    _JOBS[job_id] = {"df": df, "status": status}
    persist(job_id)
    save_raw_upload(job_id, raw, filename)
    enforce_job_capacity(protected_job_id=job_id)
    return job_id


def save_raw_upload(job_id: str, raw: bytes, filename: str):
    """Keep the exact bytes the user uploaded, untouched by `clean` or any
    later stage -- once parsed into a dataframe the original file is
    otherwise gone for good, so this is the only way to hand it back later
    (e.g. from the dashboard's "last generated file" download)."""
    from pathlib import Path

    suffix = Path(filename or "").suffix.lower() or ".csv"
    (_job_dir(job_id) / f"raw_upload{suffix}").write_bytes(raw)


def get_raw_upload_path(job_id: str):
    matches = list(_job_dir(job_id).glob("raw_upload.*"))
    return matches[0] if matches else None


def get_df(job_id: str) -> pd.DataFrame:
    _ensure_loaded(job_id)
    return _JOBS[job_id]["df"]


def set_df(job_id: str, df: pd.DataFrame):
    _ensure_loaded(job_id)
    _JOBS[job_id]["df"] = df


def set_status(job_id: str, status: dict):
    """Replace a job's whole status dict in one go -- used by checkpoints.py
    on rollback, alongside set_df, instead of that module reaching into
    _JOBS directly."""
    _ensure_loaded(job_id)
    _JOBS[job_id]["status"] = status


def get_status(job_id: str) -> dict:
    _ensure_loaded(job_id)
    status = _JOBS[job_id]["status"]
    # Snapshots created before draft support remain readable. Audit events
    # don't live in this dict at all -- see job_audit.py, they're
    # append-only in their own file.
    status.setdefault("drafts", {})
    status.setdefault("checkpoints", [])
    return status


def is_processing(job_id: str) -> bool:
    with _PROCESSING_LOCK:
        return job_id in _PROCESSING_JOBS


def persist(job_id: str):
    job = _JOBS[job_id]
    d = _job_dir(job_id)
    job["df"].to_parquet(d / "working.parquet", index=False)
    # Compact, not pretty-printed -- nobody reads this file by hand, and the
    # audit log alone can run into hundreds of thousands of entries on a
    # large job, where indent=2's per-key whitespace is pure size overhead
    # rewritten on every persist() call.
    (d / "status.json").write_text(json.dumps(job["status"], separators=(",", ":")), encoding="utf-8")


def _ensure_loaded(job_id: str):
    if job_id in _JOBS:
        return
    from .job_paths import JOB_ID_RE

    if not JOB_ID_RE.match(job_id):
        raise KeyError(job_id)
    d = JOBS_DIR / job_id
    status_file, data_file = d / "status.json", d / "working.parquet"
    if not status_file.exists() or not data_file.exists():
        raise KeyError(job_id)
    status = json.loads(status_file.read_text(encoding="utf-8"))
    df = pd.read_parquet(data_file)
    _JOBS[job_id] = {"df": df, "status": status}


def job_exists(job_id: str) -> bool:
    if job_id in _JOBS:
        return True
    from .job_paths import JOB_ID_RE

    if not JOB_ID_RE.match(job_id):
        return False
    d = JOBS_DIR / job_id
    return (d / "status.json").exists()


def delete_job(job_id: str):
    from .job_paths import JOB_ID_RE

    _JOBS.pop(job_id, None)
    with _PROGRESS_LOCK:
        _PROGRESS.pop(job_id, None)
    with _PROCESSING_LOCK:
        _PROCESSING_JOBS.discard(job_id)
    # Only called internally today (job_retention.enforce_job_capacity/
    # enforce_job_retention, with job_id straight from a real directory
    # listing), never from a route -- this check is defense-in-depth for if
    # that ever changes, not a fix for an active path today.
    if JOB_ID_RE.match(job_id):
        shutil.rmtree(JOBS_DIR / job_id, ignore_errors=True)


def list_job_summaries(stage_id: str | None = None) -> list[dict]:
    """Cheap per-job overview for the dashboard: reads status.json (small) and
    only Parquet metadata (not the full dataframe) for row counts. Pass
    `stage_id` to only return jobs currently parked at that stage (used by
    the Stage 2/3 intake pages' job pickers) -- default (None) keeps the
    unfiltered dashboard listing."""
    _ensure_jobs_dir()
    from .pipeline.registry import STAGES
    stage_title_map = {s["id"]: s["title"] for s in STAGES}

    summaries = []
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
        stage_index = status.get("stage_index", 0)
        stages = status.get("stages", [])
        stage = stages[stage_index] if isinstance(stage_index, int) and 0 <= stage_index < len(stages) else None
        if stage_id is not None and (stage is None or stage.get("id") != stage_id):
            continue
        row_count = None
        parquet_path = job_dir / "working.parquet"
        if parquet_path.exists():
            try:
                row_count = pq.ParquetFile(parquet_path).metadata.num_rows
            except Exception:
                row_count = None
        # Title comes from the live registry, not whatever was snapshotted
        # into this job's status at creation time -- see helpers.py's
        # _live_stage_title for why.
        stage_title = stage_title_map.get(stage["id"], stage["title"]) if stage else "Final Output"
        summaries.append({
            "job_id": job_dir.name,
            "filename": status.get("filename", ""),
            "created_at": status.get("created_at"),
            "stage_id": stage["id"] if stage else "done",
            "stage_title": stage_title,
            "stage": stage.get("stage") if stage else None,
            "stage_index": stage_index,
            "total_stages": len(stages),
            "is_done": _is_done(status),
            "is_processing": is_processing(job_dir.name),
            "row_count": row_count,
        })
    summaries.sort(key=lambda s: s["job_id"], reverse=True)
    return summaries
