"""Every xlsx read/write in the app: the stage-handoff dispatch files
(Stage 1/2 Naresh/Haider workbooks), the final flat output, and the
sheet/row-count summary shown under every download button. Split out of
helpers.py, which used to hold this alongside three or four unrelated
concerns (stage navigation, audit-event construction, edit validation) --
if it touches an xlsx file, it belongs here; if it doesn't, it belongs in
helpers.py instead."""
from pathlib import Path

import pandas as pd
from openpyxl.utils import get_column_letter

from . import pipeline, store
from .pipeline import stage_merge

# Default for the manual review workbook download (/api/jobs/{id}/workbook,
# in routes/stage.py) -- that route walks every manual_edit stage reached so
# far generically and doesn't know which one specifically needs notes, so it
# just uses this one flag. Off by default.
# The per-sheet stage handoff files (_write_stage1_haider_xlsx and friends,
# below) don't use this -- each sheet passes its own `notes` argument to
# _write_filtered_sheet instead, so it can be turned on for just the sheets
# that need it (see _write_stage2_haider_xlsx's DOB Corrections sheet).
VALIDATION_NOTES: bool = False


def _autofit_worksheet(worksheet, df: pd.DataFrame, *, min_width: int = 8, max_width: int = 60) -> None:
    """Size each column to its content instead of Excel's default fixed
    width, so a downloaded sheet doesn't need a manual column-resize pass."""
    for i, col in enumerate(df.columns, start=1):
        content_width = df[col].astype(str).map(len).max() if len(df) else 0
        width = max(len(str(col)), int(content_width), min_width) + 2
        worksheet.column_dimensions[get_column_letter(i)].width = min(width, max_width)


def _write_filtered_sheet(
    writer, sheet_name: str, df: pd.DataFrame, mask: pd.Series, cols: list[str], reasons_fn,
    *, notes: bool = False,
):
    """One stage-handoff sheet: ACCOUNT_NUMBER + `cols`, cut down to the
    flagged rows, optionally with a validation_notes column explaining why
    each row is there -- pass notes=True at the call site for whichever
    sheets actually need it, off by default everywhere else."""
    sheet_df = df.loc[mask]
    sheet = sheet_df[["ACCOUNT_NUMBER", *cols]].copy()
    if notes:
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
    now come from the two direct CMS exports at Stage 3.
    DOB Corrections carries notes (why each DOB is flagged) -- Haider needs
    that context on the DOB sheet specifically; Name/ID don't."""
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
            stage_merge.DOB_COLS, pipeline.validation_reasons_dob_only, notes=True,
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


def _workbook_summary(path) -> dict:
    """Sheet names + row counts for an already-written xlsx (or the raw
    upload, which may be .csv instead) -- backs the "what's actually in
    this file" preview shown next to every download button, so an operator
    can see the shape of a handoff file without downloading and opening it.
    Reads via pandas (all sheets at once for xlsx) rather than a lighter
    read-only sheet-dimension trick -- every file this looks at is a
    dispatch/review handoff (tens-hundreds of rows) or, at most, the raw
    upload (tens of thousands), so a full parse is still fast and this
    stays consistent with how every other reader in this codebase works."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        df = store.read_table(path.read_bytes(), path.name)
        return {"sheets": [{"name": path.stem, "rows": len(df)}], "total_rows": len(df)}
    sheets = pd.read_excel(path, sheet_name=None, dtype=str, engine="calamine")
    entries = [{"name": name, "rows": len(df)} for name, df in sheets.items()]
    return {"sheets": entries, "total_rows": sum(entry["rows"] for entry in entries)}
