from datetime import datetime, timezone

import pandas as pd
from fastapi import HTTPException
from openpyxl.utils import get_column_letter

from . import pipeline, store
from .pipeline import flow_merge
from .schemas import EditItem

# Flip to True to include the validation_notes column in exported Excel
# sheets (flow handoff files + manual review workbook). Off by default.
VALIDATION_NOTES: bool = False


def _autofit_worksheet(worksheet, df: pd.DataFrame, *, min_width: int = 8, max_width: int = 60) -> None:
    """Size each column to its content instead of Excel's default fixed
    width, so a downloaded sheet doesn't need a manual column-resize pass."""
    for i, col in enumerate(df.columns, start=1):
        content_width = df[col].astype(str).map(len).max() if len(df) else 0
        width = max(len(str(col)), int(content_width), min_width) + 2
        worksheet.column_dimensions[get_column_letter(i)].width = min(width, max_width)


def _current_stage(status: dict) -> dict | None:
    idx = status["stage_index"]
    if idx >= len(status["stages"]):
        return None
    return status["stages"][idx]


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
    # A real correction now comes from Flow 2/3 (Naresh/Haider) or, if
    # manual-edit stages are ever re-enabled, an operator's inline edit --
    # never from a fixed stage id or exact label string, both of which
    # stopped matching once corrections moved off the old per-field manual
    # stages. Every audit event records who made it, so "not System" is the
    # one signal that still means "a person actually fixed this."
    names_corrected = len({event["row_key"] for event in audit
                           if event.get("field") in flow_merge.NAME_COLS and event.get("operator") != "System"})
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
    """One flow-handoff sheet: ACCOUNT_NUMBER + `cols`, cut down to the
    flagged rows, with a validation_notes column explaining why each row
    is there."""
    sheet_df = df.loc[mask]
    sheet = sheet_df[["ACCOUNT_NUMBER", *cols]].copy()
    if VALIDATION_NOTES:
        reasons = reasons_fn(df)
        sheet["validation_notes"] = [", ".join(reasons.get(int(k), [])) for k in sheet_df.index]
    sheet.to_excel(writer, sheet_name=sheet_name, index=False)
    _autofit_worksheet(writer.sheets[sheet_name], sheet)


def _write_flow1_haider_xlsx(df: pd.DataFrame, out_path) -> None:
    """Flow 1 dispatch file for Haider: Name/Mobile sheets cut down to
    currently-flagged rows, plus a CMS Data Integration sheet listing every
    account with the CMS columns blank for him to fill in by hand -- CMS is
    no longer an automated upload merge, Haider is the sole source now. DOB
    goes to Naresh instead (_write_ids_and_dob_xlsx below), not here."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_filtered_sheet(
            writer, flow_merge.SHEET_NAME_VALIDATE, df, pipeline.mask_name_invalid(df),
            flow_merge.NAME_COLS, pipeline.validation_reasons_name,
        )
        _write_filtered_sheet(
            writer, flow_merge.SHEET_MISSING_MOBILE, df, pipeline.mask_mobile_missing(df),
            flow_merge.MOBILE_COLS, pipeline.validation_reasons_mobile,
        )

        cms_sheet = df[["ACCOUNT_NUMBER"]].copy()
        for col in pipeline.CMS_UPDATE_COLS:
            cms_sheet[col] = ""
        cms_sheet.to_excel(writer, sheet_name=flow_merge.SHEET_CMS, index=False)
        _autofit_worksheet(writer.sheets[flow_merge.SHEET_CMS], cms_sheet)


def _write_ids_and_dob_xlsx(df: pd.DataFrame, out_path) -> None:
    """Shared shape for Flow 2's Naresh input file and Flow 2's dispatch
    output for Haider: 2 sheets, ID Corrections + DOB Mistakes, each cut down
    to currently-invalid rows only. Naresh handles both together; whatever
    either sheet leaves invalid becomes Haider's second-pass file in Flow 3."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_filtered_sheet(
            writer, flow_merge.SHEET_ID_CORRECTIONS, df, pipeline.mask_id_only_invalid(df),
            flow_merge.ID_COLS, pipeline.validation_reasons_id_only,
        )
        _write_filtered_sheet(
            writer, flow_merge.SHEET_DOB_MISTAKES, df, pipeline.mask_dob_invalid(df),
            flow_merge.DOB_COLS, pipeline.validation_reasons_dob_only,
        )


def _write_flow1_naresh_xlsx(df: pd.DataFrame, out_path) -> None:
    _write_ids_and_dob_xlsx(df, out_path)


def _write_flow2_haider_xlsx(df: pd.DataFrame, out_path) -> None:
    _write_ids_and_dob_xlsx(df, out_path)


def _write_flat_xlsx(df: pd.DataFrame, out_path) -> None:
    """Write the current dataset as a single flat sheet with every column --
    the same shape as the original raw import. Used for the final download
    once the pipeline is done: by then there's nothing left to review, so
    the topic-split, flagged-rows-only format used for the flow handoffs
    (_write_flow1_haider_xlsx and friends) doesn't apply."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Final Output", index=False)
        _autofit_worksheet(writer.sheets["Final Output"], df)


def _upload_metrics(df: pd.DataFrame, ref_df: pd.DataFrame) -> dict:
    base_keys = df["ACCOUNT_NUMBER"].astype(str).str.strip()
    reference_keys = ref_df["ACCOUNT_NUMBER"].astype(str).str.strip()
    matched = base_keys.isin(reference_keys)
    return {
        "matched_rows": int(matched.sum()),
        "matched_accounts": int(base_keys[matched].nunique()),
        "unmatched_rows": int((~matched).sum()),
    }
