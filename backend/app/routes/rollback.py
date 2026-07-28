"""Rollback to a checkpoint.

Rolling all the way back to the initial "clean" stage (i.e. replacing the raw
source file) isn't supported: that would require re-running the entire
pipeline anyway, so starting a new job is the better path for that case.
"""
from fastapi import APIRouter, HTTPException

from .. import store
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
