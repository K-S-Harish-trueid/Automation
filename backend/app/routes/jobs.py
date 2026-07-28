"""Job lifecycle: create/import, listing, progress, backup export, final/audit downloads."""
import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import pipeline, store
from ..background import _run_in_background
from ..helpers import _current_stage, _public_job_status, _require_job, _write_stage_sheets_xlsx

router = APIRouter()


@router.post("/api/jobs")
async def create_job(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        job_id = store.create_job(raw, file.filename)
        store.create_checkpoint(job_id, "Initial imported data", pipeline.STAGES[0]["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"The backend could not write the initial rollback checkpoint: {e}") from e

    store.try_begin_processing(job_id)
    store.set_progress(
        job_id, status="processing", current_step_index=0,
        total_steps=len(pipeline.STAGES), current_step_name="Starting…", percent=0,
    )
    _run_in_background(job_id, resolve_gate=lambda: None)
    return {"job_id": job_id, "status": "processing"}


@router.post("/api/uploads/raw-preview")
async def raw_upload_preview(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        return store.preview_raw_upload(raw, file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/jobs/import")
async def import_job_backup(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        job_id = store.import_job_backup(raw, file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))

    status = store.get_status(job_id)
    stage = _current_stage(status)
    if stage is not None and stage["type"] == "auto" and not status.get("source_reopen_requested"):
        store.try_begin_processing(job_id)
        store.set_progress(
            job_id, status="processing", current_step_index=status["stage_index"],
            total_steps=len(status["stages"]), current_step_name=stage["title"],
            percent=round(status["stage_index"] / len(status["stages"]) * 100),
        )
        _run_in_background(job_id, resolve_gate=lambda: None)
        return {"job_id": job_id, "status": "processing"}

    progress_status = "done" if stage is not None and stage["type"] == "done" else "idle"
    store.set_progress(job_id, status=progress_status)
    return {"job_id": job_id, "status": progress_status}


@router.get("/api/jobs")
def list_jobs():
    return {"jobs": store.list_job_summaries()}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    return _public_job_status(_require_job(job_id))


@router.get("/api/jobs/{job_id}/progress")
def job_progress(job_id: str):
    _require_job(job_id)
    return store.get_progress(job_id)


@router.get("/api/jobs/{job_id}/export")
def export_job_backup(job_id: str):
    _require_job(job_id)
    if store.is_processing(job_id):
        raise HTTPException(409, "Wait for the current processing step before exporting a backup")
    out_path = store.export_job_backup(job_id)
    return FileResponse(out_path, filename="K2_Job_Backup.zip")


@router.get("/api/jobs/{job_id}/download")
def download_final(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "done":
        raise HTTPException(400, "pipeline is not finished yet")

    df = store.get_df(job_id)
    out_dir = store.JOBS_DIR / job_id
    out_path = out_dir / "final.xlsx"
    _write_stage_sheets_xlsx(df, out_path)
    return FileResponse(out_path, filename=f"{job_id}.xlsx")


@router.get("/api/jobs/{job_id}/audit")
def audit_history(job_id: str):
    status = _require_job(job_id)
    events = status.get("audit", [])
    return {"count": len(events), "events": events[-250:]}


@router.get("/api/jobs/{job_id}/audit/download")
def download_audit(job_id: str):
    status = _require_job(job_id)
    events = status.get("audit", [])
    columns = [
        "account_number", "field", "old_value", "new_value", "stage", "operator",
        "time", "reason", "source_file", "label",
    ]
    audit_df = pd.DataFrame(events, columns=columns)
    out_path = store.JOBS_DIR / job_id / "K2_Data_Audit.xlsx"
    audit_df.to_excel(out_path, index=False)
    return FileResponse(out_path, filename="K2_Data_Audit.xlsx")
