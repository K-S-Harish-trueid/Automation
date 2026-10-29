"""Standalone /delete page for removing finished or abandoned jobs. Same
no-auth stance as stage_test.py/seed.py (see backend/README.md's security
note) and deliberately not linked from the main app UI -- reachable only by
knowing the /delete URL.

Deleting a job removes its folder under jobs/ and that job's rows in the two
Postgres tables keyed by job_id (audit_log, generated_records). The
historical table is NOT touched: values a job wrote into historical stay,
even though those rows still carry its job_id."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text

from .. import audit_log_db, generated_records_db, store
from ..historical_db import get_engine
from ..job_paths import JOB_ID_RE

router = APIRouter()

_PAGE_PATH = Path(__file__).resolve().parent.parent / "templates" / "delete.html"
_JOB_TABLES = (audit_log_db.TABLE, generated_records_db.TABLE)


@router.get("/delete", include_in_schema=False)
def delete_page():
    return FileResponse(_PAGE_PATH)


def _folder_bytes(job_id: str) -> int:
    return sum(f.stat().st_size for f in (store.JOBS_DIR / job_id).rglob("*") if f.is_file())


def _pg_counts() -> dict[str, dict[str, int]] | None:
    """{table: {job_id: rows}} for both job tables, or None if Postgres is
    unreachable. A missing table just counts as empty."""
    counts: dict[str, dict[str, int]] = {}
    try:
        with get_engine().connect() as conn:
            for table in _JOB_TABLES:
                exists = conn.execute(text("SELECT to_regclass(:t)"), {"t": f'"{table}"'}).scalar()
                rows = conn.execute(text(f'SELECT job_id, COUNT(*) FROM "{table}" GROUP BY job_id')).all() if exists else []
                counts[table] = {job_id: int(n) for job_id, n in rows}
    except Exception:
        return None
    return counts


@router.get("/api/admin/jobs", include_in_schema=False)
def list_jobs_for_delete():
    counts = _pg_counts()
    jobs = []
    for s in store.list_job_summaries():
        jobs.append({
            **s,
            "folder_bytes": _folder_bytes(s["job_id"]),
            "audit_rows": None if counts is None else counts[audit_log_db.TABLE].get(s["job_id"], 0),
            "generated_rows": None if counts is None else counts[generated_records_db.TABLE].get(s["job_id"], 0),
        })
    return {"jobs": jobs, "postgres_ok": counts is not None}


def _delete_one(job_id: str) -> dict:
    """Postgres rows first, in one transaction: if that fails, nothing is
    deleted and the folder stays, so a job is never half removed. Refused
    while the job is processing."""
    if not JOB_ID_RE.match(job_id) or not store.job_exists(job_id):
        raise HTTPException(404, "Job not found")
    if not store.try_begin_processing(job_id):
        raise HTTPException(409, "Job is processing right now; wait for it to finish")
    deleted = {}
    try:
        with get_engine().begin() as conn:
            for table in _JOB_TABLES:
                if conn.execute(text("SELECT to_regclass(:t)"), {"t": f'"{table}"'}).scalar():
                    deleted[table] = conn.execute(text(f'DELETE FROM "{table}" WHERE job_id = :j'), {"j": job_id}).rowcount
                else:
                    deleted[table] = 0
    except Exception as e:
        store.end_processing(job_id)
        raise HTTPException(503, f"Could not delete Postgres rows, nothing was deleted: {e}")
    store.delete_job(job_id)
    return {"job_id": job_id, "audit_rows": deleted[audit_log_db.TABLE],
            "generated_rows": deleted[generated_records_db.TABLE]}


@router.delete("/api/admin/jobs/{job_id}", include_in_schema=False)
def delete_job(job_id: str):
    return _delete_one(job_id)
