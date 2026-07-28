"""Rollback to a checkpoint, and the reopened-source (replace raw file and rerun) flow."""
from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import pipeline, store
from ..background import _run_in_background
from ..helpers import _current_stage, _require_job

router = APIRouter()


@router.post("/api/jobs/{job_id}/rollback")
def rollback_job(job_id: str):
    _require_job(job_id)
    if store.is_processing(job_id):
        raise HTTPException(409, "Job is already processing")

    try:
        checkpoint = store.rollback_latest_checkpoint(job_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return _resume_from_rollback(job_id, checkpoint)


def _resume_from_rollback(job_id: str, checkpoint: dict):
    """Return a restored job to its saved gate or rerun its automatic stages."""
    restored = store.get_status(job_id)
    stage = _current_stage(restored)
    total = len(restored["stages"])
    if stage is not None and stage["type"] == "auto":
        if stage["id"] == "clean":
            restored["source_reopen_requested"] = True
            store.persist(job_id)
            store.set_progress(
                job_id, status="idle", current_step_index=0,
                total_steps=total, current_step_name=stage["title"], percent=0,
            )
            return {"job_id": job_id, "status": "idle", "restored": checkpoint["label"]}
        store.try_begin_processing(job_id)
        store.set_progress(
            job_id, status="processing", current_step_index=restored["stage_index"],
            total_steps=total, current_step_name=stage["title"],
            percent=round(restored["stage_index"] / total * 100),
        )
        _run_in_background(job_id, resolve_gate=lambda: None)
        return {"job_id": job_id, "status": "processing", "restored": checkpoint["label"]}

    progress_status = "done" if stage is not None and stage["type"] == "done" else "idle"
    store.set_progress(
        job_id, status=progress_status, current_step_index=restored["stage_index"],
        total_steps=total, current_step_name=stage["title"] if stage else "Final Output",
        percent=100 if progress_status == "done" else round(restored["stage_index"] / total * 100),
    )
    return {"job_id": job_id, "status": progress_status, "restored": checkpoint["label"]}


@router.post("/api/jobs/{job_id}/rollback/{checkpoint_id}")
def rollback_to_stage(job_id: str, checkpoint_id: str):
    _require_job(job_id)
    if store.is_processing(job_id):
        raise HTTPException(409, "Job is already processing")
    try:
        checkpoint = store.rollback_to_checkpoint(job_id, checkpoint_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _resume_from_rollback(job_id, checkpoint)


def _begin_initial_cleaning(job_id: str, status: dict):
    """Snapshot the selected source, then restart the unchanged automatic pipeline."""
    if not store.try_begin_processing(job_id):
        raise HTTPException(409, "Job is already processing")
    try:
        store.create_checkpoint(job_id, "Initial imported data", pipeline.STAGES[0]["id"])
    except OSError as e:
        store.end_processing(job_id)
        raise HTTPException(500, f"The backend could not write a rollback checkpoint: {e}") from e

    status.pop("source_reopen_requested", None)
    total = len(status["stages"])
    store.set_progress(
        job_id, status="processing", current_step_index=0,
        total_steps=total, current_step_name="Starting...", percent=0,
    )
    _run_in_background(job_id, resolve_gate=lambda: None)
    return {"job_id": job_id, "status": "processing"}


@router.post("/api/jobs/{job_id}/resume-source")
def resume_reopened_source(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["id"] != "clean" or not status.get("source_reopen_requested"):
        raise HTTPException(400, "the initial source stage is not awaiting review")
    return _begin_initial_cleaning(job_id, status)


@router.post("/api/jobs/{job_id}/source")
async def replace_reopened_source(job_id: str, file: UploadFile = File(...)):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["id"] != "clean" or not status.get("source_reopen_requested"):
        raise HTTPException(400, "the initial source stage is not awaiting review")

    raw = await file.read()
    try:
        source_df = store.read_table(raw, file.filename or "Raw K2 export")
    except ValueError as e:
        raise HTTPException(400, str(e))
    missing = [column for column in pipeline.RAW_REQUIRED_COLS if column not in source_df.columns]
    if missing:
        raise HTTPException(400, f"Uploaded file missing required columns: {missing}")

    source_df = source_df.reset_index(drop=True)
    status["filename"] = file.filename or "Raw K2 export"
    status["stage_index"] = 0
    status["stages"] = [{**stage_template, "status": "pending"} for stage_template in pipeline.STAGES]
    status["history"] = []
    status["audit"] = []
    status["drafts"] = {}
    status["checkpoints"] = []
    store.set_df(job_id, source_df)
    return _begin_initial_cleaning(job_id, status)
