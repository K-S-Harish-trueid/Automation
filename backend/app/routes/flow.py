"""The Flow 1/2/3 reviewer handoff. Each flow is its own self-contained
mini-pipeline, not one long linear chain:

  Flow 1 (the normal job wizard): clean...mobile_fill -> Flow 1 Dispatch.
    Hands out two files and stops -- done, nothing advances it further from
    inside Flow 1 itself.
  Flow 2 (this file's naresh-response endpoint, driven from a separate
    picker page): input Naresh's file -> merge IDs -> verify invalid count
    -> Flow 2 Dispatch (hands out the leftover-ID file for Haider).
  Flow 3 (this file's haider-response endpoint, another separate picker
    page): input Haider's two files -> merge into the raw data -> the
    existing final_id_check/default_id gates (final ID scan, then the
    confirm-click default-ID assignment) -> final output. Flow 3 doesn't
    stop at its own screen -- once its upload is applied, the frontend hands
    the job to the normal per-job wizard, which already knows how to walk
    through confirm -> done.

Routes below are grouped by which flow's *action* they serve, not by which
stage_id happens to be current when they're called -- e.g. naresh-response
lives under /flow2/ because it's Flow 2's core action, even though it
resolves the flow1_dispatch stage."""
import io

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import pipeline, store
from ..background import _run_in_background
from ..helpers import (
    _append_audit_events,
    _current_stage,
    _require_job,
    _write_flow1_haider_xlsx,
    _write_flow1_naresh_xlsx,
    _write_flow2_haider_xlsx,
)
from ..pipeline import flow_merge

router = APIRouter()


def _read_workbook_sheets(raw: bytes, filename: str) -> dict[str, pd.DataFrame]:
    try:
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, dtype=str, engine="calamine")
    except Exception as e:
        raise ValueError(f"Could not read Excel file {filename}: {e}") from e
    return {name: df.fillna("") for name, df in sheets.items()}


def _begin_gate(job_id: str, status: dict, stage: dict):
    """Same begin_processing -> checkpoint -> set_progress sequence every
    other gate-resolving route in stage.py uses."""
    if not store.try_begin_processing(job_id):
        raise HTTPException(409, "Job is already processing")
    try:
        store.create_checkpoint(job_id, f"Before {stage['title']}", stage["id"])
    except OSError as e:
        store.end_processing(job_id)
        raise HTTPException(500, f"The backend could not write a rollback checkpoint: {e}") from e

    total = len(status["stages"])
    store.set_progress(
        job_id, status="processing", current_step_index=status["stage_index"] + 1,
        total_steps=total, current_step_name=stage["title"],
        percent=round(status["stage_index"] / total * 100),
    )


# ---- Flow 1: dispatch downloads -------------------------------------------

@router.get("/api/jobs/{job_id}/flow1/haider.xlsx")
def download_flow1_haider(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "flow1":
        raise HTTPException(400, "current stage does not offer this download")
    if store.is_processing(job_id):
        raise HTTPException(409, "Wait for the current processing step before downloading")

    df = store.get_df(job_id)
    out_path = store.JOBS_DIR / job_id / "flow1_haider.xlsx"
    _write_flow1_haider_xlsx(df, out_path)
    return FileResponse(out_path, filename=f"{job_id}_flow1_haider.xlsx")


@router.get("/api/jobs/{job_id}/flow1/naresh.xlsx")
def download_flow1_naresh(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "flow1":
        raise HTTPException(400, "current stage does not offer this download")
    if store.is_processing(job_id):
        raise HTTPException(409, "Wait for the current processing step before downloading")

    df = store.get_df(job_id)
    out_path = store.JOBS_DIR / job_id / "flow1_naresh.xlsx"
    _write_flow1_naresh_xlsx(df, out_path)
    return FileResponse(out_path, filename=f"{job_id}_flow1_naresh.xlsx")


# ---- Flow 2: input -> merge -> verify -> dispatch -------------------------

@router.post("/api/jobs/{job_id}/flow2/naresh-response")
async def apply_naresh_response(job_id: str, file: UploadFile = File(...)):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "flow1":
        raise HTTPException(400, "this job is not waiting on a Flow 2 input")

    raw = await file.read()
    filename = file.filename or "Naresh's response"
    try:
        resp_df = store.read_table(raw, filename)
        flow_merge.validate_naresh_response(resp_df)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _begin_gate(job_id, status, stage)

    def resolve_gate():
        df = store.get_df(job_id)
        before = df.copy(deep=True)
        st = store.get_status(job_id)
        cur = _current_stage(st)
        df, summary, matched = flow_merge.apply_naresh_response(df, resp_df)
        remaining = int(pipeline.mask_id_only_invalid(df).sum())
        _append_audit_events(
            st, before, df, stage_id=cur["id"], fields=flow_merge.ID_COLS,
            label="Naresh corrected", reason="ID correction supplied by Naresh (Flow 2 input).",
            source_file=filename, operator="Naresh",
        )
        cur["status"] = "done"
        st["history"].append({
            "stage_id": cur["id"], "title": cur["title"],
            "summary": f"{summary} {remaining} account(s) still invalid.",
            # remaining_invalid drives Flow 2's own completion message and
            # lets Flow 3 know whether this job even needs an IDs file.
            "metrics": {"matched_rows": matched, "remaining_invalid": remaining},
        })
        st["stage_index"] += 1
        store.set_df(job_id, df)

    _run_in_background(job_id, resolve_gate)
    return {"job_id": job_id, "status": "processing"}


@router.get("/api/jobs/{job_id}/flow2/haider.xlsx")
def download_flow2_haider(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "flow2":
        raise HTTPException(400, "current stage does not offer this download")
    if store.is_processing(job_id):
        raise HTTPException(409, "Wait for the current processing step before downloading")

    df = store.get_df(job_id)
    out_path = store.JOBS_DIR / job_id / "flow2_haider.xlsx"
    _write_flow2_haider_xlsx(df, out_path)
    return FileResponse(out_path, filename=f"{job_id}_flow2_haider.xlsx")


# ---- Flow 3: input -> merge -> (final_id_check/default_id, unchanged) -----

@router.post("/api/jobs/{job_id}/flow3/haider-response")
async def apply_haider_response(
    job_id: str,
    corrections_file: UploadFile = File(...),
    # Optional: if Naresh already resolved every invalid ID, there's nothing
    # left for Haider's IDs file to fix -- no flow2_haider.xlsx worth acting
    # on, so no input should be required here either. Enforced below against
    # the job's actual current invalid count, not just trusted from the client.
    ids_file: UploadFile | None = File(None),
):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "flow2":
        raise HTTPException(400, "this job is not waiting on a Flow 3 input")

    still_invalid = int(pipeline.mask_id_only_invalid(store.get_df(job_id)).sum())
    if ids_file is None and still_invalid > 0:
        raise HTTPException(
            400,
            f"{still_invalid} account(s) still have invalid IDs -- Haider's IDs response file is required",
        )

    corrections_raw = await corrections_file.read()
    corrections_filename = corrections_file.filename or "Haider's corrections response"
    ids_raw = None
    ids_filename = None
    if ids_file is not None:
        ids_raw = await ids_file.read()
        ids_filename = ids_file.filename or "Haider's IDs response"

    try:
        sheets = _read_workbook_sheets(corrections_raw, corrections_filename)
        flow_merge.validate_haider_corrections_response(sheets)
        ids_resp_df = None
        if ids_raw is not None:
            ids_resp_df = store.read_table(ids_raw, ids_filename)
            flow_merge.validate_haider_ids_response(ids_resp_df)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _begin_gate(job_id, status, stage)

    def resolve_gate():
        df = store.get_df(job_id)
        before_all = df.copy(deep=True)
        st = store.get_status(job_id)
        cur = _current_stage(st)

        df, corrections_summary, counts = flow_merge.apply_haider_corrections_response(df, sheets)
        after_corrections = df.copy(deep=True)
        _append_audit_events(
            st, before_all, after_corrections, stage_id=cur["id"],
            fields=[*flow_merge.NAME_COLS, *flow_merge.DOB_COLS, *flow_merge.MOBILE_COLS, *pipeline.CMS_UPDATE_COLS],
            label="Haider corrected", reason="Correction supplied by Haider's response.",
            source_file=corrections_filename, operator="Haider",
        )
        # Fed under the "cms_integration" stage id (that stage no longer
        # exists in STAGES) purely so _quality_summary's existing
        # _history_metrics(status, "cms_integration") lookup keeps working
        # now that Haider, not an uploaded export, is the CMS data source.
        st["history"].append({
            "stage_id": "cms_integration", "title": "CMS Data Integration", "summary": corrections_summary,
            "metrics": {"matched_rows": counts["cms_matched"], "unmatched_rows": len(after_corrections) - counts["cms_matched"]},
        })

        if ids_resp_df is not None:
            df, ids_summary, ids_matched = flow_merge.apply_haider_ids_response(df, ids_resp_df)
            _append_audit_events(
                st, after_corrections, df, stage_id=cur["id"], fields=flow_merge.ID_COLS,
                label="Haider corrected (IDs)", reason="ID correction supplied by Haider's IDs response.",
                source_file=ids_filename, operator="Haider",
            )
        else:
            ids_summary = "No IDs were still invalid -- no IDs file was needed."
            ids_matched = 0

        cur["status"] = "done"
        st["history"].append({
            "stage_id": cur["id"], "title": cur["title"],
            "summary": f"{corrections_summary} {ids_summary}",
            "metrics": {"matched_rows": ids_matched},
        })
        st["stage_index"] += 1
        store.set_df(job_id, df)

    _run_in_background(job_id, resolve_gate)
    return {"job_id": job_id, "status": "processing"}
