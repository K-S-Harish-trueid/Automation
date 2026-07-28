from datetime import datetime, timezone

import pandas as pd
from fastapi import HTTPException
from openpyxl.utils import get_column_letter

from . import pipeline, store
from .schemas import EditItem


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
    names_corrected = len({event["row_key"] for event in audit
                           if event.get("stage") == "name_validate" and event.get("label") == "Operator corrected"})
    generated_ids = len({event["row_key"] for event in audit
                         if event.get("stage") == "default_id" and event.get("field") == "ID_NUMBER"})
    operator_corrections = len({(event["row_key"], event["field"]) for event in audit
                                if event.get("label") == "Operator corrected"})
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


# (stage_id, columns) pairs for the Send Email reviewer handoff -- one named
# sheet per manual-edit stage topic, plus the CMS exception. Sheet names come
# from the matching STAGES title so they stay in sync with the registry.
_REVIEW_SHEET_COLUMNS = [
    ("name_validate", ["ACCOUNT_FIRST_NAME", "ACCOUNT_MIDDLE_NAME", "ACCOUNT_LAST_NAME"]),
    ("id_dob_validate", ["ID_TYPE", "ID_NUMBER", "ACCOUNT_HOLDER_DOB"]),
    ("mobile_fill", ["PHONE_NUMBER"]),
    ("cms_integration", ["CARD_NUMBER", "ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]),
    ("final_id_check", ["ID_TYPE", "ID_NUMBER"]),
]


def _validation_notes_column(df: pd.DataFrame, stage_id: str) -> pd.Series | None:
    """Per-row 'why is this flagged' text for sheets that map to a manual_edit
    stage's validator/reasons pair; None for sheets with no such rule (e.g.
    CMS Data Integration)."""
    cfg = pipeline.MANUAL_STAGES.get(stage_id)
    if not cfg:
        return None
    reasons = cfg["reasons"](df)
    return pd.Series(
        [", ".join(reasons.get(int(row_key), [])) for row_key in df.index],
        index=df.index,
    )


def _write_review_sheets_xlsx(df: pd.DataFrame, out_path) -> None:
    """Write the Send Email reviewer handoff: one sheet per manual-edit stage
    topic (Name Validation, ID & DoB, Mobile, Final ID), cut down to only
    that stage's flagged rows with a 'validation_notes' column explaining why
    -- since manual-edit stages are bypassed and an outside reviewer needs to
    know what to fix without the tool's UI. CMS Data Integration is the one
    exception: it's a merge report, not a validation gate, so it keeps every
    row, unfiltered, with no notes column."""
    titles = {s["id"]: s["title"] for s in pipeline.STAGES}
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for stage_id, cols in _REVIEW_SHEET_COLUMNS:
            cfg = pipeline.MANUAL_STAGES.get(stage_id)
            available = [c for c in cols if c in df.columns]
            if not available:
                continue
            sheet_df = df.loc[cfg["validator"](df)] if cfg else df
            sheet = sheet_df[["ACCOUNT_NUMBER", *available]].copy()
            if cfg:
                sheet["validation_notes"] = _validation_notes_column(sheet_df, stage_id).values
            sheet_name = titles.get(stage_id, stage_id)[:31]
            sheet.to_excel(writer, sheet_name=sheet_name, index=False)
            _autofit_worksheet(writer.sheets[sheet_name], sheet)


def _write_flat_xlsx(df: pd.DataFrame, out_path) -> None:
    """Write the current dataset as a single flat sheet with every column --
    the same shape as the original raw import. Used for the final download
    once the pipeline is done: by then there's nothing left to review, so
    the topic-split, flagged-rows-only format used for the Send Email
    handoff (_write_review_sheets_xlsx) doesn't apply."""
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
