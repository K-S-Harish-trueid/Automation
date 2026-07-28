"""In-memory job store with a disk-backed snapshot for crash recovery / download."""
import io
import json
import os
import shutil
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import pandas as pd
import pyarrow.parquet as pq

JOBS_DIR = Path(__file__).resolve().parent.parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

def _positive_limit_from_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


# Job folders contain KYC data, so the local workspace works as a rolling
# store. Once it fills up, the oldest non-processing job is removed for the
# new one. Set K2_MAX_STORED_JOBS to adjust the default capacity.
MAX_STORED_JOBS = _positive_limit_from_env("K2_MAX_STORED_JOBS", 100)
MAX_DONE_JOBS = MAX_STORED_JOBS

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


def _job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    d.mkdir(exist_ok=True)
    return d


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
            df = pd.read_excel(io.BytesIO(raw), dtype=str).fillna("")
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
        "audit": [],
        "drafts": {},
        "checkpoints": [],
    }
    _JOBS[job_id] = {"df": df, "status": status}
    persist(job_id)
    enforce_job_capacity(protected_job_id=job_id)
    return job_id


def get_df(job_id: str) -> pd.DataFrame:
    _ensure_loaded(job_id)
    return _JOBS[job_id]["df"]


def set_df(job_id: str, df: pd.DataFrame):
    _ensure_loaded(job_id)
    _JOBS[job_id]["df"] = df


def get_status(job_id: str) -> dict:
    _ensure_loaded(job_id)
    status = _JOBS[job_id]["status"]
    # Snapshots created before audit/draft support remain readable.
    status.setdefault("audit", [])
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
    (d / "status.json").write_text(json.dumps(job["status"], indent=2), encoding="utf-8")


def create_checkpoint(job_id: str, label: str, stage_id: str | None = None) -> dict:
    """Save the current job state before a user-triggered pipeline action."""
    _ensure_loaded(job_id)
    job = _JOBS[job_id]
    status = get_status(job_id)
    stage_index = status["stage_index"]
    stage = status["stages"][stage_index] if stage_index < len(status["stages"]) else {}
    checkpoint_id = f"cp-{len(status['checkpoints']) + 1:02d}-{uuid.uuid4().hex[:6]}"
    checkpoint_dir = _job_dir(job_id) / "checkpoints" / checkpoint_id
    checkpoint_dir.mkdir(parents=True, exist_ok=False)

    job["df"].to_parquet(checkpoint_dir / "working.parquet", index=False)
    checkpoint_status = deepcopy(status)
    (checkpoint_dir / "status.json").write_text(
        json.dumps(checkpoint_status, indent=2), encoding="utf-8"
    )
    metadata = {
        "id": checkpoint_id,
        "label": label,
        "stage_id": stage_id or stage.get("id", ""),
        "stage_index": stage_index,
        "stage_title": stage.get("title", ""),
    }
    status["checkpoints"].append(metadata)
    persist(job_id)
    return metadata


def get_rollback_targets(job_id: str) -> list[dict]:
    """Return valid checkpoint metadata, including legacy snapshots without stage metadata."""
    _ensure_loaded(job_id)
    status = get_status(job_id)
    targets = []
    for checkpoint in status.get("checkpoints", []):
        checkpoint_dir = _job_dir(job_id) / "checkpoints" / checkpoint["id"]
        status_path = checkpoint_dir / "status.json"
        data_path = checkpoint_dir / "working.parquet"
        if not status_path.exists() or not data_path.exists():
            continue
        try:
            snapshot = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stage_index = snapshot.get("stage_index")
        stages = snapshot.get("stages", [])
        if not isinstance(stage_index, int) or not 0 <= stage_index < len(stages):
            continue
        stage = stages[stage_index]
        targets.append({
            "id": checkpoint["id"],
            "label": checkpoint.get("label", "Saved checkpoint"),
            "stage_id": stage.get("id", checkpoint.get("stage_id", "")),
            "stage_index": stage_index,
            "stage_title": stage.get("title", checkpoint.get("stage_title", "")),
        })
    return targets


def rollback_to_checkpoint(job_id: str, checkpoint_id: str) -> dict:
    """Restore a selected checkpoint and remove every checkpoint after it."""
    _ensure_loaded(job_id)
    status = get_status(job_id)
    checkpoints = status.get("checkpoints", [])
    checkpoint_index = next(
        (index for index, checkpoint in enumerate(checkpoints) if checkpoint.get("id") == checkpoint_id),
        None,
    )
    if checkpoint_index is None:
        raise ValueError("The selected rollback checkpoint is no longer available")

    metadata = checkpoints[checkpoint_index]
    checkpoint_dir = _job_dir(job_id) / "checkpoints" / metadata["id"]
    status_path = checkpoint_dir / "status.json"
    data_path = checkpoint_dir / "working.parquet"
    if not status_path.exists() or not data_path.exists():
        raise ValueError("The latest rollback checkpoint is incomplete")

    restored_status = json.loads(status_path.read_text(encoding="utf-8"))
    restored_status["job_id"] = job_id
    # Downstream work was created from the state being replaced, so neither
    # its audit trail nor its later restore points remain valid.
    restored_status["checkpoints"] = checkpoints[:checkpoint_index]
    restored_status.setdefault("audit", [])
    restored_status.setdefault("drafts", {})
    _JOBS[job_id] = {"df": pd.read_parquet(data_path), "status": restored_status}
    for discarded in checkpoints[checkpoint_index:]:
        shutil.rmtree(_job_dir(job_id) / "checkpoints" / discarded["id"], ignore_errors=True)
    persist(job_id)
    return metadata


def rollback_latest_checkpoint(job_id: str) -> dict:
    """Restore and consume the latest checkpoint so repeated rollback steps backwards."""
    _ensure_loaded(job_id)
    checkpoints = get_status(job_id).get("checkpoints", [])
    if not checkpoints:
        raise ValueError("No rollback checkpoint is available for this job")
    return rollback_to_checkpoint(job_id, checkpoints[-1]["id"])


def export_job_backup(job_id: str) -> Path:
    """Create a portable archive of the current job state, without old checkpoints."""
    _ensure_loaded(job_id)
    persist(job_id)
    job_dir = _job_dir(job_id)
    out_path = job_dir / f"K2_Job_Backup_{job_id}.zip"
    with ZipFile(out_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(job_dir / "working.parquet", "working.parquet")
        archive.write(job_dir / "status.json", "status.json")
    return out_path


def import_job_backup(raw: bytes, filename: str) -> str:
    """Restore a portable job backup into a new local job ID."""
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            names = set(archive.namelist())
            required = {"working.parquet", "status.json"}
            if not required.issubset(names):
                raise ValueError("Backup must contain working.parquet and status.json")
            status_raw = archive.read("status.json")
            data_raw = archive.read("working.parquet")
    except BadZipFile as e:
        raise ValueError("The selected file is not a valid K2 job backup ZIP") from e
    except KeyError as e:
        raise ValueError("The selected backup is missing required K2 job files") from e

    try:
        imported_status = json.loads(status_raw.decode("utf-8"))
        df = pd.read_parquet(io.BytesIO(data_raw))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError) as e:
        raise ValueError("The K2 job backup could not be read") from e

    from .pipeline import RAW_REQUIRED_COLS, STAGES

    missing = [column for column in RAW_REQUIRED_COLS if column not in df.columns]
    if missing:
        raise ValueError(f"Imported backup is missing required columns: {missing}")
    imported_stages = imported_status.get("stages")
    if not isinstance(imported_stages, list) or [s.get("id") for s in imported_stages] != [s["id"] for s in STAGES]:
        raise ValueError("This backup was created by an incompatible K2 pipeline version")
    stage_index = imported_status.get("stage_index")
    if not isinstance(stage_index, int) or not 0 <= stage_index <= len(STAGES):
        raise ValueError("The imported backup has an invalid pipeline position")

    job_id = _next_job_id()
    valid_stage_statuses = {"pending", "current", "done"}
    stages = []
    for template, imported_stage in zip(STAGES, imported_stages):
        stage_status = imported_stage.get("status", "pending")
        if stage_status not in valid_stage_statuses:
            raise ValueError("The imported backup has an invalid stage status")
        stages.append({**template, "status": stage_status})

    status = {
        "job_id": job_id,
        "filename": imported_status.get("filename") or filename or "Imported K2 job",
        "created_at": time.time(),
        "stage_index": stage_index,
        "stages": stages,
        "history": imported_status.get("history") if isinstance(imported_status.get("history"), list) else [],
        "audit": imported_status.get("audit") if isinstance(imported_status.get("audit"), list) else [],
        "drafts": imported_status.get("drafts") if isinstance(imported_status.get("drafts"), dict) else {},
        "checkpoints": [],
    }
    _JOBS[job_id] = {"df": df.reset_index(drop=True), "status": status}
    persist(job_id)
    enforce_job_capacity(protected_job_id=job_id)
    return job_id


def _ensure_loaded(job_id: str):
    if job_id in _JOBS:
        return
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
    d = JOBS_DIR / job_id
    return (d / "status.json").exists()


def _is_done(status: dict) -> bool:
    idx = status.get("stage_index", 0)
    stages = status.get("stages", [])
    if idx >= len(stages):
        return True
    stage = stages[idx]
    return stage.get("type") == "done" and stage.get("status") == "done"


def delete_job(job_id: str):
    _JOBS.pop(job_id, None)
    with _PROGRESS_LOCK:
        _PROGRESS.pop(job_id, None)
    with _PROCESSING_LOCK:
        _PROCESSING_JOBS.discard(job_id)
    shutil.rmtree(JOBS_DIR / job_id, ignore_errors=True)


def list_job_summaries() -> list[dict]:
    """Cheap per-job overview for the dashboard: reads status.json (small) and
    only Parquet metadata (not the full dataframe) for row counts."""
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
        row_count = None
        parquet_path = job_dir / "working.parquet"
        if parquet_path.exists():
            try:
                row_count = pq.ParquetFile(parquet_path).metadata.num_rows
            except Exception:
                row_count = None
        summaries.append({
            "job_id": job_dir.name,
            "filename": status.get("filename", ""),
            "created_at": status.get("created_at"),
            "stage_title": stage["title"] if stage else "Final Output",
            "stage_index": stage_index,
            "total_stages": len(stages),
            "is_done": _is_done(status),
            "is_processing": is_processing(job_dir.name),
            "row_count": row_count,
        })
    summaries.sort(key=lambda s: s["job_id"], reverse=True)
    return summaries


def _stored_job_records() -> list[tuple[float, str]]:
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
    records = _stored_job_records()
    over_capacity = len(records) - max_stored_jobs
    if over_capacity <= 0:
        return

    for _, job_id in sorted(records):
        if over_capacity <= 0:
            break
        if job_id == protected_job_id or is_processing(job_id):
            continue
        delete_job(job_id)
        over_capacity -= 1


def enforce_job_retention(max_done_jobs: int = MAX_DONE_JOBS):
    """Keeps at most `max_done_jobs` completed jobs on disk (and in memory),
    deleting the oldest ones first. Jobs still waiting on a manual gate are
    never counted or touched, however many of them exist."""
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
        if _is_done(status):
            done.append((status_file.stat().st_mtime, d.name))

    done.sort(key=lambda t: t[0], reverse=True)  # newest first
    for _, job_id in done[max_done_jobs:]:
        delete_job(job_id)
