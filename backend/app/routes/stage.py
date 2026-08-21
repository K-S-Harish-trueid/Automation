"""The interactive per-stage gate flow: viewing the current stage, resolving
upload/manual_edit/confirm gates (JSON, inline edits, or the Excel workbook
round-trip), and drafts."""
import io
import os

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import generated_records_db, historical_db, pipeline, store
from ..background import _run_in_background
from ..helpers import (
    _append_audit_events,
    _current_stage,
    _phase_progress,
    _quality_summary,
    _require_job,
    _upload_metrics,
    _validate_edit_items,
)
from ..schemas import DraftRequest, EditItem, SubmitRequest
from ..xlsx_export import VALIDATION_NOTES, _autofit_worksheet, _workbook_summary

router = APIRouter()

ROW_PAGE_SIZE = 200

# Toggle: while False, manual_edit stages are view-only -- inline field edits
# (JSON /submit with edits, /draft) are rejected and the frontend renders the
# table read-only. The Excel workbook round-trip (/workbook, /submit-workbook)
# and force-advancing past a stage (/submit with force_advance and no edits)
# stay available either way. Flip back to True to re-enable inline editing;
# nothing tied to it has been removed.
MANUAL_EDIT_ENABLED = False


@router.get("/api/jobs/{job_id}/current")
def current_stage_detail(job_id: str, page: int = 1):
    status = _require_job(job_id)
    df = store.get_df(job_id)
    stage = _current_stage(status)

    if stage is None or stage["type"] == "done":
        # One read of the audit log, reused for the quality summary's tallies
        # and the preview/count below, instead of three separate reads.
        audit = store.read_audit_events(job_id)
        return {
            "type": "done",
            "row_count": len(df),
            "quality_summary": _quality_summary(df, status, audit),
            "audit_event_count": len(audit),
            "audit_preview": audit[-30:],
        }

    if stage["type"] == "upload":
        return {
            "type": "upload",
            "stage_id": stage["id"],
            "title": stage["title"],
            "label": stage["upload_label"],
            "guidance": stage.get("upload_guidance", {}),
            "api_invoke_planned": bool(stage.get("api_invoke_planned")),
        }

    if stage["type"] in ("stage1", "stage2"):
        payload = {
            "type": stage["type"],
            "stage_id": stage["id"],
            "title": stage["title"],
            "row_count": len(df),
        }
        if stage["type"] == "stage2":
            # Lets the wait screen (and Stage 3) tell the operator whether
            # there's anything left for Haider's second-pass file to fix --
            # if both are 0, Stage 3 doesn't need that file for this job at all.
            payload["invalid_id_count"] = int(pipeline.mask_id_only_invalid(df).sum())
            payload["invalid_dob_count"] = int(pipeline.mask_dob_invalid(df).sum())
            payload["invalid_name_count"] = int(pipeline.mask_name_invalid(df).sum())
        return payload

    # Paused mid-auto-chain by background.py because historical.db was
    # empty/unseeded when the "replace" stage ran -- not a static stage type
    # in the registry, just this job's stage_index sitting still with
    # status["pending_warning"] set until /continue-historical clears it.
    pending_warning = status.get("pending_warning")
    if stage["type"] == "auto" and pending_warning and pending_warning["stage_id"] == stage["id"]:
        return {
            "type": "historical_warning",
            "stage_id": stage["id"],
            "title": stage["title"],
            "message": pending_warning["message"],
        }

    if stage["type"] == "confirm":
        preview_fn = pipeline.CONFIRM_PREVIEW.get(stage["id"])
        count = preview_fn(df) if preview_fn else 0
        return {
            "type": "confirm",
            "stage_id": stage["id"],
            "title": stage["title"],
            "affected_count": count,
            "summary": f"{count} record(s) will be affected by this step.",
        }

    if stage["type"] == "manual_edit":
        cfg = pipeline.MANUAL_STAGES[stage["id"]]
        mask = cfg["validator"](df)
        flagged = df.loc[mask, cfg["context_cols"] + cfg["editable_cols"]].copy()
        total = len(flagged)
        page_count = max(1, (total + ROW_PAGE_SIZE - 1) // ROW_PAGE_SIZE)
        page = min(max(page, 1), page_count)
        start = (page - 1) * ROW_PAGE_SIZE
        page_df = flagged.iloc[start:start + ROW_PAGE_SIZE].copy()
        page_df.insert(0, "row_key", page_df.index)
        reasons = cfg["reasons"](df)
        visible_fields = set(cfg["context_cols"] + cfg["editable_cols"])
        labels_by_row: dict[int, list[str]] = {}
        for event in store.read_audit_events(job_id):
            row_key = event.get("row_key")
            if row_key in page_df.index and event.get("field") in visible_fields and event.get("label"):
                labels_by_row.setdefault(row_key, []).append(event["label"])
        rows = page_df.to_dict(orient="records")
        for row in rows:
            row_key = int(row["row_key"])
            row["validation_reasons"] = reasons.get(row_key, [])
            labels = labels_by_row.get(row_key, [])
            if row["validation_reasons"]:
                labels = [*labels, "Unverified"]
            row["data_labels"] = list(dict.fromkeys(labels))
        return {
            "type": "manual_edit",
            "stage_id": stage["id"],
            "title": stage["title"],
            "manual_edit_enabled": MANUAL_EDIT_ENABLED,
            "instructions": cfg["instructions"],
            "editable_cols": cfg["editable_cols"],
            "context_cols": cfg["context_cols"],
            "total_flagged": total,
            "showing": len(page_df),
            "page": page,
            "page_count": page_count,
            "rows_remaining": max(0, total - (start + len(page_df))),
            "draft_edits": status.get("drafts", {}).get(stage["id"], []),
            "rows": rows,
        }

    return {"type": "auto"}


@router.post("/api/jobs/{job_id}/upload")
async def upload_reference(job_id: str, file: UploadFile = File(...)):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "upload":
        raise HTTPException(400, "current stage does not accept an upload")

    raw = await file.read()
    reference_filename = file.filename or "Uploaded reference file"
    try:
        ref_df = store.read_table(raw, reference_filename)
    except ValueError as e:
        raise HTTPException(400, str(e))

    try:
        pipeline.validate_upload_inputs(stage["id"], store.get_df(job_id), ref_df)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if not store.try_begin_processing(job_id):
        raise HTTPException(409, "Job is already processing")
    try:
        store.create_checkpoint(job_id, f"Before {stage['title']}", stage["id"])
    except OSError as e:
        store.end_processing(job_id)
        raise HTTPException(500, f"The backend could not write a rollback checkpoint: {e}") from e

    step_index, total, percent = _phase_progress(status, status["stage_index"])
    store.set_progress(
        job_id, status="processing", current_step_index=step_index,
        total_steps=total, current_step_name=stage["title"],
        percent=percent,
    )

    def resolve_gate():
        df = store.get_df(job_id)
        before = df.copy(deep=True)
        st = store.get_status(job_id)
        cur = _current_stage(st)
        handler = pipeline.UPLOAD_HANDLERS[cur["id"]]
        df, summary = handler(df, ref_df=ref_df)
        metrics = _upload_metrics(before, ref_df)
        _append_audit_events(
            st, before, df, stage_id=cur["id"], label="Source-file updated",
            reason="Matched value supplied by the uploaded reference file.",
            source_file=reference_filename, operator="System",
        )
        cur["status"] = "done"
        st["history"].append({
            "stage_id": cur["id"], "title": cur["title"], "summary": summary, "metrics": metrics,
        })
        st["stage_index"] += 1
        store.set_df(job_id, df)

    _run_in_background(job_id, resolve_gate)
    return {"job_id": job_id, "status": "processing"}


@router.post("/api/jobs/{job_id}/update-historical")
def update_historical(job_id: str):
    """Insert-or-replace this completed job's accounts into the historical
    SQL store (historical_db.py) -- keyed on ACCOUNT_NUMBER via
    ON CONFLICT DO UPDATE, so calling this again later (even on the same
    job) never duplicates rows, it just refreshes them (and re-stamps which
    job/when). Only available once the job is finished. Fast enough (bulk
    insert, no xlsx parse) to run synchronously in the request instead of
    needing progress tracking."""
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is not None and stage["type"] != "done":
        raise HTTPException(400, "Job must be complete before updating the historical store")

    df = store.get_df(job_id)
    try:
        updated = historical_db.upsert_rows(df, job_id=job_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"updated_rows": updated}


@router.post("/api/jobs/{job_id}/continue-historical")
def continue_without_historical(job_id: str):
    """Resumes a job paused at the historical-override step because
    historical.db was empty/unseeded (background.py's pending_warning
    pause) -- the operator has seen the warning and chosen to proceed. If
    they seeded historical.db first, the re-run of stage_replace_from_sql
    below applies the real override, so this needs the same rollback
    checkpoint every other gate-resolving route takes before touching data.
    Marks the job acknowledged so the auto-stage loop won't pause here again
    once it re-runs and finalizes this stage, then keeps going through the
    rest of the pipeline exactly like resuming any other gate."""
    status = _require_job(job_id)
    stage = _current_stage(status)
    pending_warning = status.get("pending_warning")
    if stage is None or not pending_warning or pending_warning["stage_id"] != stage["id"]:
        raise HTTPException(400, "No pending historical-data warning to continue past")

    if not store.try_begin_processing(job_id):
        raise HTTPException(409, "Job is already processing")
    try:
        store.create_checkpoint(job_id, f"Before {stage['title']}", stage["id"])
    except OSError as e:
        store.end_processing(job_id)
        raise HTTPException(500, f"The backend could not write a rollback checkpoint: {e}") from e

    step_index, total, percent = _phase_progress(status, status["stage_index"])
    store.set_progress(
        job_id, status="processing", current_step_index=step_index,
        total_steps=total, current_step_name=stage["title"],
        percent=percent,
    )

    def resolve_gate():
        st = store.get_status(job_id)
        st["historical_ack"] = True
        st.pop("pending_warning", None)

    _run_in_background(job_id, resolve_gate)
    return {"job_id": job_id, "status": "processing"}


@router.post("/api/jobs/{job_id}/draft")
def save_draft(job_id: str, body: DraftRequest):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "manual_edit":
        raise HTTPException(400, "current stage does not accept a draft")
    if not MANUAL_EDIT_ENABLED:
        raise HTTPException(403, "Manual inline editing is disabled right now. Use the Excel review workbook instead.")
    if store.is_processing(job_id):
        raise HTTPException(409, "Job is already processing")

    cfg = pipeline.MANUAL_STAGES[stage["id"]]
    _validate_edit_items(store.get_df(job_id), cfg, body.edits)
    status.setdefault("drafts", {})[stage["id"]] = [edit.model_dump() for edit in body.edits]
    store.persist(job_id)
    return {"saved": len(body.edits), "stage_id": stage["id"]}


@router.delete("/api/jobs/{job_id}/draft")
def clear_draft(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "manual_edit":
        raise HTTPException(400, "current stage does not have a draft")
    status.setdefault("drafts", {}).pop(stage["id"], None)
    store.persist(job_id)
    return {"cleared": True}


def _write_and_get_workbook_path(job_id: str):
    status = _require_job(job_id)
    if store.is_processing(job_id):
        raise HTTPException(409, "Wait for the current processing step before downloading the review workbook")

    df = store.get_df(job_id)
    reached = [
        stage for i, stage in enumerate(status["stages"])
        if stage["type"] == "manual_edit" and i <= status["stage_index"]
    ]
    if not reached:
        raise HTTPException(400, "No manual review stage has been reached yet.")

    out_path = store.JOBS_DIR / job_id / "K2_Manual_Review.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for stage in reached:
            cfg = pipeline.MANUAL_STAGES[stage["id"]]
            mask = cfg["validator"](df)
            flagged = df.loc[mask, cfg["context_cols"] + cfg["editable_cols"]].copy()
            flagged.insert(0, "row_key", flagged.index)
            if VALIDATION_NOTES:
                reasons = cfg["reasons"](df)
                flagged["validation_notes"] = [", ".join(reasons.get(int(k), [])) for k in flagged["row_key"]]
            sheet_name = stage["title"][:31]
            flagged.to_excel(writer, sheet_name=sheet_name, index=False)
            _autofit_worksheet(writer.sheets[sheet_name], flagged)
    return out_path


@router.get("/api/jobs/{job_id}/workbook")
def download_workbook(job_id: str):
    out_path = _write_and_get_workbook_path(job_id)
    return FileResponse(out_path, filename="K2_Manual_Review.xlsx")


@router.get("/api/jobs/{job_id}/workbook/summary")
def summary_workbook(job_id: str):
    out_path = _write_and_get_workbook_path(job_id)
    return _workbook_summary(out_path)


def _submit_manual_edits(
    job_id: str, status: dict, stage: dict, edits: list[EditItem], force_advance: bool,
    source_file: str = "Operator input",
):
    """Shared by the JSON /submit endpoint and the Excel /submit-workbook
    endpoint: validates, checkpoints, applies edits, audits, and advances the
    stage once fully resolved (or force_advance)."""
    cfg = pipeline.MANUAL_STAGES[stage["id"]]
    df = store.get_df(job_id)
    _validate_edit_items(df, cfg, edits)

    if not store.try_begin_processing(job_id):
        raise HTTPException(409, "Job is already processing")
    try:
        store.create_checkpoint(job_id, f"Before {stage['title']}", stage["id"])
    except OSError as e:
        store.end_processing(job_id)
        raise HTTPException(500, f"The backend could not write a rollback checkpoint: {e}") from e

    step_index, total, percent = _phase_progress(status, status["stage_index"])
    store.set_progress(
        job_id, status="processing", current_step_index=step_index,
        total_steps=total, current_step_name=stage["title"],
        percent=percent,
    )

    def resolve_gate():
        df = store.get_df(job_id)
        before = df.copy(deep=True)
        st = store.get_status(job_id)
        cur = _current_stage(st)
        cfg2 = pipeline.MANUAL_STAGES[cur["id"]]

        for edit in edits:
            df.at[edit.row_key, edit.field] = edit.value
        _append_audit_events(
            st, before, df, stage_id=cur["id"], label="Operator corrected",
            reason="Manual review correction.", source_file=source_file,
            operator=os.environ.get("K2_OPERATOR_NAME", "Local operator"),
        )
        st.setdefault("drafts", {}).pop(cur["id"], None)
        store.set_df(job_id, df)

        remaining = int(cfg2["validator"](df).sum())
        if remaining == 0 or force_advance:
            cur["status"] = "done"
            st["history"].append({
                "stage_id": cur["id"],
                "title": cur["title"],
                "summary": f"{len(edits)} field(s) edited. {remaining} row(s) still flagged when advanced.",
                "metrics": {"operator_edits": len(edits), "remaining_flagged": remaining},
            })
            st["stage_index"] += 1

    _run_in_background(job_id, resolve_gate)


@router.post("/api/jobs/{job_id}/submit")
def submit_edits(job_id: str, body: SubmitRequest):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "manual_edit":
        raise HTTPException(400, "current stage is not a manual-edit stage")
    if not MANUAL_EDIT_ENABLED and body.edits:
        raise HTTPException(403, "Manual inline editing is disabled right now. Use the Excel review workbook instead.")
    _submit_manual_edits(job_id, status, stage, body.edits, body.force_advance)
    return {"job_id": job_id, "status": "processing"}


@router.post("/api/jobs/{job_id}/submit-workbook")
async def submit_workbook(job_id: str, file: UploadFile = File(...), force_advance: bool = Form(False)):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "manual_edit":
        raise HTTPException(400, "current stage is not a manual-edit stage")

    cfg = pipeline.MANUAL_STAGES[stage["id"]]
    raw = await file.read()
    try:
        sheet_df = pd.read_excel(
            io.BytesIO(raw), sheet_name=stage["title"], dtype=str, engine="calamine"
        ).fillna("")
    except ValueError as e:
        raise HTTPException(
            400, f"Workbook is missing the '{stage['title']}' sheet for the current stage."
        ) from e
    except Exception as e:
        raise HTTPException(400, f"Could not read uploaded workbook: {e}") from e

    if "row_key" not in sheet_df.columns:
        raise HTTPException(400, "Uploaded sheet is missing the row_key column.")

    edits: list[EditItem] = []
    for _, row in sheet_df.iterrows():
        try:
            row_key = int(row["row_key"])
        except (TypeError, ValueError):
            raise HTTPException(400, f"Uploaded sheet has an invalid row_key value: {row['row_key']!r}")
        for field in cfg["editable_cols"]:
            if field in sheet_df.columns:
                edits.append(EditItem(row_key=row_key, field=field, value=str(row[field])))

    _submit_manual_edits(
        job_id, status, stage, edits, force_advance,
        source_file=file.filename or "Uploaded review workbook",
    )
    return {"job_id": job_id, "status": "processing"}


@router.post("/api/jobs/{job_id}/confirm")
def confirm_stage(job_id: str):
    status = _require_job(job_id)
    stage = _current_stage(status)
    if stage is None or stage["type"] != "confirm":
        raise HTTPException(400, "current stage is not a confirm stage")

    if not store.try_begin_processing(job_id):
        raise HTTPException(409, "Job is already processing")
    try:
        store.create_checkpoint(job_id, f"Before {stage['title']}", stage["id"])
    except OSError as e:
        store.end_processing(job_id)
        raise HTTPException(500, f"The backend could not write a rollback checkpoint: {e}") from e

    step_index, total, percent = _phase_progress(status, status["stage_index"])
    store.set_progress(
        job_id, status="processing", current_step_index=step_index,
        total_steps=total, current_step_name=stage["title"],
        percent=percent,
    )

    def resolve_gate():
        df = store.get_df(job_id)
        before = df.copy(deep=True)
        st = store.get_status(job_id)
        cur = _current_stage(st)
        handler = pipeline.CONFIRM_HANDLERS[cur["id"]]
        df, summary = handler(df)
        generated_count = int(pipeline.default_id_invalid_mask(before).sum()) if cur["id"] == "default_id" else 0
        events = _append_audit_events(
            st, before, df, stage_id=cur["id"], fields=["ID_TYPE", "ID_NUMBER"], label="Generated",
            reason="Generated Civil ID assigned after the final ID validation step.",
            source_file="System generated", operator="System",
        )
        generated_records_db.record_generated(job_id, events)
        cur["status"] = "done"
        st["history"].append({
            "stage_id": cur["id"], "title": cur["title"], "summary": summary,
            "metrics": {"generated_ids": generated_count},
        })
        st["stage_index"] += 1
        store.set_df(job_id, df)

    _run_in_background(job_id, resolve_gate)
    return {"job_id": job_id, "status": "processing"}
