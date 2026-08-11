"""The Flow 1/2/3 reviewer handoff. Each flow is its own self-contained
mini-pipeline, not one long linear chain:

  Flow 1 (the normal job wizard): clean...mobile_fill -> Flow 1 Dispatch.
    Hands out two files and stops -- done, nothing advances it further from
    inside Flow 1 itself. Naresh gets IDs + DOB, Haider gets Mobile/CMS.
  Flow 2 (this file's naresh-response endpoint, driven from a separate
    picker page): input Naresh's ID+DOB file -> merge both -> verify both
    still-invalid counts -> Flow 2 Dispatch (generates Haider's Stage 2 file).
  Flow 3 (this file's haider-response endpoint, another separate picker
    page): input three files -- CMS Mobile Numbers, CMS Card Details, and
    Haider's Stage 2 Corrected file (Name/ID/DOB) -- merge all three into
    the raw data -> the existing final_id_check/default_id gates (final ID
    scan, then the confirm-click default-ID assignment) -> final output.
    Flow 3 doesn't stop at its own screen -- once its upload is applied,
    the frontend hands the job to the normal per-job wizard.

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

def _download_frozen_dispatch_file(job_id: str, out_name: str, expected_stage_type: str, writer):
    """Serve a flow dispatch file as a permanent artifact of the job instead
    of regenerating it from the live dataframe on every call: the first
    request (made while the job is actually parked at the matching stage)
    freezes it to disk, and every request after that -- from any later
    stage, including after the job is fully done -- just serves that same
    frozen copy. Without this, the file becomes unrecoverable the moment the
    job advances (the live dataframe has already been overwritten by
    whoever's response resolved that stage), and the dashboard's "last
    generated file" download would have nothing stable to point at."""
    status = _require_job(job_id)
    out_path = store.JOBS_DIR / job_id / out_name
    if not out_path.exists():
        stage = _current_stage(status)
        if stage is None or stage["type"] != expected_stage_type:
            raise HTTPException(400, "current stage does not offer this download")
        if store.is_processing(job_id):
            raise HTTPException(409, "Wait for the current processing step before downloading")
        writer(store.get_df(job_id), out_path)
    return FileResponse(out_path, filename=f"{job_id}_{out_name}")


@router.get("/api/jobs/{job_id}/flow1/haider.xlsx")
def download_flow1_haider(job_id: str):
    return _download_frozen_dispatch_file(job_id, "flow1_haider.xlsx", "flow1", _write_flow1_haider_xlsx)


@router.get("/api/jobs/{job_id}/flow1/naresh.xlsx")
def download_flow1_naresh(job_id: str):
    return _download_frozen_dispatch_file(job_id, "flow1_naresh.xlsx", "flow1", _write_flow1_naresh_xlsx)


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
        sheets = _read_workbook_sheets(raw, filename)
        flow_merge.validate_naresh_response(sheets)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _begin_gate(job_id, status, stage)

    def resolve_gate():
        df = store.get_df(job_id)
        before = df.copy(deep=True)
        st = store.get_status(job_id)
        cur = _current_stage(st)
        df, summary, counts = flow_merge.apply_naresh_response(df, sheets)
        remaining_name = int(pipeline.mask_name_invalid(df).sum())
        remaining_id = int(pipeline.mask_id_only_invalid(df).sum())
        remaining_dob = int(pipeline.mask_dob_invalid(df).sum())
        _append_audit_events(
            st, before, df, stage_id=cur["id"], fields=[*flow_merge.ID_COLS, *flow_merge.DOB_COLS],
            label="Naresh corrected", reason="ID/DOB correction supplied by Naresh (Flow 2 input).",
            source_file=filename, operator="Naresh",
        )
        cur["status"] = "done"
        st["history"].append({
            "stage_id": cur["id"], "title": cur["title"],
            "summary": f"{summary} {remaining_name} Name, {remaining_id} ID and {remaining_dob} DOB account(s) still invalid.",
            # remaining_invalid_id/dob drive Flow 2's own completion message
            # and let Flow 3 know whether this job even needs a second-pass file.
            "metrics": {
                "matched_rows": counts["id_matched"] + counts["dob_matched"],
                "id_matched": counts["id_matched"], "dob_matched": counts["dob_matched"],
                "remaining_invalid_name": remaining_name,
                "remaining_invalid_id": remaining_id, "remaining_invalid_dob": remaining_dob,
            },
            "sub_steps": [
                {"label": "Merge ID & DOB corrections",
                 "detail": f"{counts['id_matched']} ID, {counts['dob_matched']} DOB account(s) updated"},
                {"label": "Name Validation", "detail": f"{remaining_name} account(s) invalid"},
                {"label": "ID Validation", "detail": f"{remaining_id} account(s) still invalid"},
                {"label": "DOB Validation", "detail": f"{remaining_dob} account(s) still invalid"},
                {"label": "Dispatch to Haider", "detail": "flow2_haider.xlsx generated"},
            ],
        })
        st["stage_index"] += 1
        store.set_df(job_id, df)

    _run_in_background(job_id, resolve_gate)
    return {"job_id": job_id, "status": "processing"}


@router.get("/api/jobs/{job_id}/flow2/haider.xlsx")
def download_flow2_haider(job_id: str):
    return _download_frozen_dispatch_file(job_id, "flow2_haider.xlsx", "flow2", _write_flow2_haider_xlsx)


# ---- Flow 3: three-file input -> merge -> (final_id_check/default_id) ------

@router.post("/api/jobs/{job_id}/flow3/haider-response")
async def apply_haider_response(
    job_id: str,
    # File 1: CMS Mobile Numbers export (flat table: ACCOUNT_NUMBER + PHONE_NUMBER)
    cms_mobile_file: UploadFile = File(...),
    # File 2: CMS Card Details export (flat table: ACCOUNT_NUMBER + card columns +
    # DATE_OPENED in M/D/YYYY format)
    cms_card_file: UploadFile = File(...),
    # File 3: Haider's Stage 2 corrected workbook (3 sheets: Name Validation /
    # ID Corrections / DOB Corrections)
    haider_stage2_file: UploadFile = File(...),
):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "flow2":
        raise HTTPException(400, "this job is not waiting on a Flow 3 input")

    # Read all three files
    cms_mobile_raw = await cms_mobile_file.read()
    cms_mobile_filename = cms_mobile_file.filename or "CMS Mobile Numbers"
    cms_card_raw = await cms_card_file.read()
    cms_card_filename = cms_card_file.filename or "CMS Card Details"
    haider_stage2_raw = await haider_stage2_file.read()
    haider_stage2_filename = haider_stage2_file.filename or "Haider Stage 2 Corrected"

    try:
        df_mobile = store.read_table(cms_mobile_raw, cms_mobile_filename)
        flow_merge.validate_cms_mobile_response(df_mobile)
        df_card = store.read_table(cms_card_raw, cms_card_filename)
        flow_merge.validate_cms_card_response(df_card)
        haider_sheets = _read_workbook_sheets(haider_stage2_raw, haider_stage2_filename)
        flow_merge.validate_haider_stage2_response(haider_sheets)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _begin_gate(job_id, status, stage)

    def resolve_gate():
        df = store.get_df(job_id)
        st = store.get_status(job_id)
        cur = _current_stage(st)

        # --- 1. Apply CMS Mobile Numbers ---
        before_mobile = df.copy(deep=True)
        df, mobile_summary, mobile_counts = flow_merge.apply_cms_mobile_response(df, df_mobile)
        after_mobile = df.copy(deep=True)
        _append_audit_events(
            st, before_mobile, after_mobile, stage_id=cur["id"],
            fields=flow_merge.MOBILE_COLS,
            label="CMS updated", reason="Mobile number supplied by CMS Mobile Numbers export.",
            source_file=cms_mobile_filename, operator="System",
        )

        # --- 2. Apply CMS Card Details ---
        before_card = after_mobile.copy(deep=True)
        df, card_summary, card_counts = flow_merge.apply_cms_card_response(df, df_card)
        after_card = df.copy(deep=True)
        _append_audit_events(
            st, before_card, after_card, stage_id=cur["id"],
            fields=pipeline.CMS_UPDATE_COLS,
            label="CMS updated", reason="Card details supplied by CMS Card Details export.",
            source_file=cms_card_filename, operator="System",
        )
        # Keep _history_metrics(status, "cms_integration") working for _quality_summary
        st["history"].append({
            "stage_id": "cms_integration", "title": "CMS Data Integration",
            "summary": f"{mobile_summary} {card_summary}",
            "metrics": {
                "matched_rows": card_counts["cms_matched"],
                "unmatched_rows": len(after_card) - card_counts["cms_matched"],
            },
        })

        # --- 3. Apply Haider Stage 2 Corrected file ---
        before_haider = after_card.copy(deep=True)
        df, haider_summary, haider_counts = flow_merge.apply_haider_stage2_response(df, haider_sheets)
        after_haider = df.copy(deep=True)
        _append_audit_events(
            st, before_haider, after_haider, stage_id=cur["id"],
            fields=[*flow_merge.NAME_COLS, *flow_merge.ID_COLS, *flow_merge.DOB_COLS],
            label="Haider corrected", reason="Correction supplied by Haider's Stage 2 response.",
            source_file=haider_stage2_filename, operator="Haider",
        )

        cur["status"] = "done"
        st["history"].append({
            "stage_id": cur["id"], "title": cur["title"],
            "summary": f"{mobile_summary} {card_summary} {haider_summary}",
            "metrics": {
                "matched_rows": mobile_counts["mobile_matched"] + card_counts["cms_matched"],
                "mobile_matched": mobile_counts["mobile_matched"],
                "cms_matched": card_counts["cms_matched"],
                "name_matched": haider_counts["name_matched"],
                "id_matched": haider_counts["id_matched"],
                "dob_matched": haider_counts["dob_matched"],
            },
            "sub_steps": [
                {"label": "CMS Mobile Numbers",
                 "detail": f"{mobile_counts['mobile_matched']} account(s) updated"},
                {"label": "CMS Card Details",
                 "detail": f"{card_counts['cms_matched']} account(s) updated"},
                {"label": "Haider Stage 2 corrections",
                 "detail": (
                     f"{haider_counts['name_matched']} name, "
                     f"{haider_counts['id_matched']} ID, "
                     f"{haider_counts['dob_matched']} DOB account(s) updated"
                 )},
            ],
        })
        st["stage_index"] += 1
        store.set_df(job_id, df)

    _run_in_background(job_id, resolve_gate)
    return {"job_id": job_id, "status": "processing"}
