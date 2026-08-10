from datetime import datetime, timezone

import pandas as pd
from fastapi import HTTPException
from openpyxl.utils import get_column_letter

from . import pipeline, store
from .pipeline import stage_merge
from .schemas import EditItem

# Flip to True to include the validation_notes column in exported Excel
# sheets (stage handoff files + manual review workbook). Off by default.
VALIDATION_NOTES: bool = False


def _autofit_worksheet(worksheet, df: pd.DataFrame, *, min_width: int = 8, max_width: int = 60) -> None:
    """Size each column to its content instead of Excel's default fixed
    width, so a downloaded sheet doesn't need a manual column-resize pass."""
    for i, col in enumerate(df.columns, start=1):
        content_width = df[col].astype(str).map(len).max() if len(df) else 0
        width = max(len(str(col)), int(content_width), min_width) + 2
        worksheet.column_dimensions[get_column_letter(i)].width = min(width, max_width)


def _live_stage_title(stage_id: str) -> str | None:
    from .pipeline.registry import STAGES
    return {s["id"]: s["title"] for s in STAGES}.get(stage_id)


def _current_stage(status: dict) -> dict | None:
    idx = status["stage_index"]
    if idx >= len(status["stages"]):
        return None
    # The real stage dict, not a copy -- callers mutate this in place
    # (cur["status"] = "done", history entries keyed off cur["id"]) and rely
    # on that persisting when the job status is saved. Re-resolve the title
    # from the live registry instead of trusting whatever was snapshotted
    # into this job's status at creation time -- otherwise a job created
    # before a stage got renamed keeps showing the old title forever. This
    # also self-heals the on-disk snapshot the next time it's saved, which
    # is a feature, not a side effect: no reason to keep serving a stale
    # title once we know the live one.
    stage = status["stages"][idx]
    live_title = _live_stage_title(stage.get("id"))
    if live_title:
        stage["title"] = live_title
    return stage


def _require_job(job_id: str) -> dict:
    if not store.job_exists(job_id):
        raise HTTPException(404, "job not found")
    return store.get_status(job_id)


def _public_job_status(status: dict) -> dict:
    """Keep large audit and draft data out of the polling response."""
    rollback_targets = store.get_rollback_targets(status["job_id"])
    public_status = {
        key: value for key, value in status.items()
        if key not in {"audit", "drafts", "checkpoints"}
    }
    if "stages" in public_status:
        public_status["stages"] = [
            {**s, "title": _live_stage_title(s.get("id")) or s["title"]}
            for s in public_status["stages"]
        ]
    return public_status | {
        "audit_event_count": len(status.get("audit", [])),
        "rollback_available": bool(rollback_targets),
        "rollback_label": rollback_targets[-1]["label"] if rollback_targets else "",
        "rollback_targets": rollback_targets,
    }


def _audit_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _append_audit_events(
    status: dict,
    before: pd.DataFrame,
    after: pd.DataFrame,
    *,
    stage_id: str,
    fields: list[str] | None = None,
    label: str | dict[str, str],
    reason: str | dict[str, str],
    source_file: str,
    operator: str,
):
    """Store field-level changes without changing any pipeline decision or value."""
    fields = fields or [column for column in after.columns if column in before.columns]
    account_numbers = after.get("ACCOUNT_NUMBER", pd.Series("", index=after.index))
    events = []
    for field in fields:
        if field not in before.columns or field not in after.columns:
            continue
        before_values = before[field].map(_audit_text)
        after_values = after[field].map(_audit_text)
        changed = before_values.ne(after_values)
        for row_key in after.index[changed]:
            event_label = label.get(field, "System corrected") if isinstance(label, dict) else label
            event_reason = reason.get(field, "Value updated") if isinstance(reason, dict) else reason
            events.append({
                "row_key": int(row_key),
                "account_number": _audit_text(account_numbers.loc[row_key]),
                "field": field,
                "old_value": before_values.loc[row_key],
                "new_value": after_values.loc[row_key],
                "stage": stage_id,
                "operator": operator,
                "time": datetime.now(timezone.utc).isoformat(),
                "reason": event_reason,
                "source_file": source_file,
                "label": event_label,
            })
    status.setdefault("audit", []).extend(events)
    return events


def _history_metrics(status: dict, stage_id: str) -> dict:
    for event in reversed(status.get("history", [])):
        if event.get("stage_id") == stage_id:
            return event.get("metrics", {})
    return {}


def _quality_summary(df: pd.DataFrame, status: dict) -> dict:
    audit = status.get("audit", [])
    # A real correction now comes from Stage 2/3 (Naresh/Haider) or, if
    # manual-edit stages are ever re-enabled, an operator's inline edit --
    # never from a fixed stage id or exact label string, both of which
    # stopped matching once corrections moved off the old per-field manual
    # stages. Every audit event records who made it, so "not System" is the
    # one signal that still means "a person actually fixed this."
    names_corrected = len({event["row_key"] for event in audit
                           if event.get("field") in stage_merge.NAME_COLS and event.get("operator") != "System"})
    generated_ids = len({event["row_key"] for event in audit
                         if event.get("stage") == "default_id" and event.get("field") == "ID_NUMBER"})
    operator_corrections = len({(event["row_key"], event["field"]) for event in audit
                                if event.get("operator") != "System"})
    cms = _history_metrics(status, "cms_integration")
    remaining = {
        "invalid_names_remaining": int(pipeline.mask_name_invalid(df).sum()),
        "invalid_dobs_remaining": int((~pipeline.compute_dob_validity(df)).sum()),
        "invalid_addresses_remaining": int(pipeline.mask_address_invalid(df).sum()),
        "missing_phones_remaining": int(pipeline.mask_mobile_missing(df).sum()),
        "invalid_ids_remaining": int(pipeline.mask_id_only_invalid(df).sum()),
    }
    has_exceptions = any(remaining.values())
    return {
        "total_rows": int(len(df)),
        "names_corrected": names_corrected,
        "generated_ids_assigned": generated_ids,
        "operator_corrections": operator_corrections,
        "cms_matches": int(cms.get("matched_rows", 0)),
        "cms_unmatched": int(cms.get("unmatched_rows", 0)),
        "status": "COMPLETED WITH EXCEPTIONS" if has_exceptions else "PASSED",
        **remaining,
    }


def _validate_edit_items(df: pd.DataFrame, cfg: dict, edits: list[EditItem]):
    for edit in edits:
        if edit.field not in cfg["editable_cols"]:
            raise HTTPException(400, f"field '{edit.field}' is not editable at this stage")
        if edit.row_key not in df.index:
            raise HTTPException(400, f"row_key {edit.row_key} not found")


def _write_filtered_sheet(writer, sheet_name: str, df: pd.DataFrame, mask: pd.Series, cols: list[str], reasons_fn):
    """One stage-handoff sheet: ACCOUNT_NUMBER + `cols`, cut down to the
    flagged rows, with a validation_notes column explaining why each row
    is there."""
    sheet_df = df.loc[mask]
    sheet = sheet_df[["ACCOUNT_NUMBER", *cols]].copy()
    if VALIDATION_NOTES:
        reasons = reasons_fn(df)
        sheet["validation_notes"] = [", ".join(reasons.get(int(k), [])) for k in sheet_df.index]
    sheet.to_excel(writer, sheet_name=sheet_name, index=False)
    _autofit_worksheet(writer.sheets[sheet_name], sheet)


def _write_stage1_haider_xlsx(df: pd.DataFrame, out_path) -> None:
    """Stage 1 dispatch file for Haider: Mobile sheet cut down to
    currently-flagged rows, plus a CMS Data Integration sheet listing every
    account with the CMS columns blank for him to fill in by hand as a
    starting point -- the real CMS mobile/card data arrives later as its own
    two export files in Stage 3 and takes precedence over anything here. DOB
    goes to Naresh instead (_write_ids_and_dob_xlsx below), not here. Name
    validation hasn't run yet at this point in the pipeline -- it's deferred
    to Stage 2 (see registry.py's STAGES), so it isn't in this file either."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_filtered_sheet(
            writer, stage_merge.SHEET_MISSING_MOBILE, df, pipeline.mask_mobile_missing(df),
            stage_merge.MOBILE_COLS, pipeline.validation_reasons_mobile,
        )

        cms_sheet = df[["ACCOUNT_NUMBER"]].copy()
        for col in pipeline.CMS_UPDATE_COLS:
            cms_sheet[col] = ""
        cms_sheet.to_excel(writer, sheet_name=stage_merge.SHEET_CMS, index=False)
        _autofit_worksheet(writer.sheets[stage_merge.SHEET_CMS], cms_sheet)


def _write_ids_and_dob_xlsx(df: pd.DataFrame, out_path) -> None:
    """Shared shape for Stage 2's Naresh input file and Stage 2's dispatch
    output for Haider: 2 sheets, ID Corrections + DOB Corrections, each cut
    down to currently-invalid rows only. Naresh handles both together;
    whatever either sheet leaves invalid becomes Haider's second-pass file
    in Stage 3."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_filtered_sheet(
            writer, stage_merge.SHEET_ID_CORRECTIONS, df, pipeline.mask_id_only_invalid(df),
            stage_merge.ID_COLS, pipeline.validation_reasons_id_only,
        )
        _write_filtered_sheet(
            writer, stage_merge.SHEET_DOB_CORRECTIONS, df, pipeline.mask_dob_invalid(df),
            stage_merge.DOB_COLS, pipeline.validation_reasons_dob_only,
        )


def _write_stage1_naresh_xlsx(df: pd.DataFrame, out_path) -> None:
    _write_ids_and_dob_xlsx(df, out_path)


def _write_stage2_haider_xlsx(df: pd.DataFrame, out_path) -> None:
    """Stage 2 dispatch file for Haider: Name Validation, ID Corrections, and
    DOB Corrections sheets -- everything still invalid after Naresh's pass,
    plus the name issues deferred from Stage 1. CMS/Mobile aren't here; those
    now come from the two direct CMS exports at Stage 3."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_filtered_sheet(
            writer, stage_merge.SHEET_NAME_VALIDATE, df, pipeline.mask_name_invalid(df),
            stage_merge.NAME_COLS, pipeline.validation_reasons_name,
        )
        _write_filtered_sheet(
            writer, stage_merge.SHEET_ID_CORRECTIONS, df, pipeline.mask_id_only_invalid(df),
            stage_merge.ID_COLS, pipeline.validation_reasons_id_only,
        )
        _write_filtered_sheet(
            writer, stage_merge.SHEET_DOB_CORRECTIONS, df, pipeline.mask_dob_invalid(df),
            stage_merge.DOB_COLS, pipeline.validation_reasons_dob_only,
        )


def _write_flat_xlsx(df: pd.DataFrame, out_path) -> None:
    """Write the current dataset as a single flat sheet with every column --
    the same shape as the original raw import. Used for the final download
    once the pipeline is done: by then there's nothing left to review, so
    the topic-split, flagged-rows-only format used for the stage handoffs
    (_write_stage1_haider_xlsx and friends) doesn't apply.
    SMART_IDENTIFIER is dropped here -- it's an internal reference used only
    to help Naresh match ID documents, not part of the delivered dataset."""
    out_df = df.drop(columns=["SMART_IDENTIFIER"], errors="ignore")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        out_df.to_excel(writer, sheet_name="Final Output", index=False)
        _autofit_worksheet(writer.sheets["Final Output"], out_df)


def _upload_metrics(df: pd.DataFrame, ref_df: pd.DataFrame) -> dict:
    base_keys = df["ACCOUNT_NUMBER"].astype(str).str.strip()
    reference_keys = ref_df["ACCOUNT_NUMBER"].astype(str).str.strip()
    matched = base_keys.isin(reference_keys)
    return {
        "matched_rows": int(matched.sum()),
        "matched_accounts": int(base_keys[matched].nunique()),
        "unmatched_rows": int((~matched).sum()),
    }
