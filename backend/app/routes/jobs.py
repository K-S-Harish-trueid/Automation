"""Job lifecycle: create, listing, progress, final/audit downloads."""
import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import pipeline, store
from ..background import _run_in_background
from ..helpers import _autofit_worksheet, _current_stage, _public_job_status, _require_job, _write_flat_xlsx

router = APIRouter()


@router.post("/api/jobs")
async def create_job(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        job_id = store.create_job(raw, file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))

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


@router.get("/api/jobs")
def list_jobs(stage_id: str | None = None):
    return {"jobs": store.list_job_summaries(stage_id)}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    return _public_job_status(_require_job(job_id))


@router.get("/api/jobs/{job_id}/progress")
def job_progress(job_id: str):
    _require_job(job_id)
    return store.get_progress(job_id)


@router.get("/api/jobs/{job_id}/raw-upload")
def download_raw_upload(job_id: str):
    _require_job(job_id)
    path = store.get_raw_upload_path(job_id)
    if path is None:
        raise HTTPException(404, "Raw upload file is not available for this job")
    return FileResponse(path, filename=f"{job_id}_raw_upload{path.suffix}")


@router.get("/api/jobs/{job_id}/download")
def download_final(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "done":
        raise HTTPException(400, "pipeline is not finished yet")

    df = store.get_df(job_id)
    out_dir = store.JOBS_DIR / job_id
    out_path = out_dir / "final.xlsx"
    _write_flat_xlsx(df, out_path)
    return FileResponse(out_path, filename=f"{job_id}_final.xlsx")


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
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        audit_df.to_excel(writer, sheet_name="Sheet1", index=False)
        _autofit_worksheet(writer.sheets["Sheet1"], audit_df)
    return FileResponse(out_path, filename=f"{job_id}_audit.xlsx")
