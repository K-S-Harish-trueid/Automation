"""Apply/validate logic for the Flow 1/2/3 reviewer handoff (Naresh does IDs
+ DOB first, Haider does Name/Mobile/CMS plus a second pass on whatever IDs
and DOBs Naresh couldn't resolve). Pure pandas -- no FastAPI here, see
routes/flow.py for the HTTP glue and helpers.py for the matching xlsx
writers.

Every merge here matches by ACCOUNT_NUMBER (string-stripped) and only
overwrites a cell where the response actually provides a non-blank value --
partial completion is expected (a reviewer won't fix every row), so a blank
cell means "leave the current value alone", never "clear it"."""
import pandas as pd

from .stages.cms_integration import CMS_UPDATE_COLS

KEY = "ACCOUNT_NUMBER"

ID_COLS = ["ID_TYPE", "ID_NUMBER"]
NAME_COLS = ["ACCOUNT_FIRST_NAME", "ACCOUNT_MIDDLE_NAME", "ACCOUNT_LAST_NAME"]
DOB_COLS = ["ACCOUNT_HOLDER_DOB"]
MOBILE_COLS = ["PHONE_NUMBER"]

SHEET_ID_CORRECTIONS = "ID Corrections"
SHEET_NAME_VALIDATE = "Name Validation"
SHEET_DOB_MISTAKES = "DOB Mistakes"
SHEET_MISSING_MOBILE = "Missing Mobile Numbers"
SHEET_CMS = "CMS Data Integration"

# Shared by Naresh's Flow 2 response and Haider's Flow 3 second-pass response
# -- both are the exact same 2-sheet shape (IDs + DOB), just submitted by a
# different reviewer at a different point in the handoff.
IDS_AND_DOB_SHEETS = {SHEET_ID_CORRECTIONS: ID_COLS, SHEET_DOB_MISTAKES: DOB_COLS}


def _require_columns(df: pd.DataFrame, columns: list[str], *, sheet: str | None = None):
    missing = [c for c in [KEY, *columns] if c not in df.columns]
    if missing:
        where = f" in the '{sheet}' sheet" if sheet else ""
        raise ValueError(f"Uploaded file is missing columns{where}: {missing}")


def _require_sheets(sheets: dict[str, pd.DataFrame] | None, expected: dict[str, list[str]], *, missing_msg: str):
    if not sheets:
        raise ValueError(missing_msg)
    missing_sheets = [name for name in expected if name not in sheets]
    if missing_sheets:
        raise ValueError(f"Uploaded workbook is missing sheet(s): {missing_sheets}")
    for name, columns in expected.items():
        _require_columns(sheets[name], columns, sheet=name)


def _overwrite_nonblank(df: pd.DataFrame, resp: pd.DataFrame, columns: list[str]) -> int:
    """Match by ACCOUNT_NUMBER, overwrite `columns` on `df` from `resp` cell
    by cell, but only where the response's value is non-blank. Returns the
    count of accounts that had at least one non-blank value applied."""
    df[KEY] = df[KEY].astype(str).str.strip()
    resp = resp.copy()
    resp[KEY] = resp[KEY].astype(str).str.strip()
    resp = resp[[KEY, *columns]].drop_duplicates(subset=[KEY], keep="first")

    touched = pd.Series(False, index=df.index)
    for col in columns:
        values = resp[col].astype(str).str.strip()
        provided = resp.loc[values.ne(""), [KEY, col]]
        if provided.empty:
            continue
        value_map = dict(zip(provided[KEY], provided[col]))
        matched = df[KEY].isin(value_map)
        df.loc[matched, col] = df.loc[matched, KEY].map(value_map)
        touched |= matched

    return int(touched.sum())


def _apply_ids_and_dob(df: pd.DataFrame, sheets: dict[str, pd.DataFrame]) -> dict:
    id_matched = _overwrite_nonblank(df, sheets[SHEET_ID_CORRECTIONS], ID_COLS)
    dob_matched = _overwrite_nonblank(df, sheets[SHEET_DOB_MISTAKES], DOB_COLS)
    return {"id_matched": id_matched, "dob_matched": dob_matched}


# ---- Flow 2: Naresh's ID + DOB response (merge) ---------------------------

def validate_naresh_response(sheets: dict[str, pd.DataFrame] | None):
    _require_sheets(sheets, IDS_AND_DOB_SHEETS, missing_msg="Naresh's response file is required")


def apply_naresh_response(df: pd.DataFrame, sheets: dict[str, pd.DataFrame]):
    validate_naresh_response(sheets)
    counts = _apply_ids_and_dob(df, sheets)
    summary = (
        f"Applied Naresh's corrections: {counts['id_matched']} ID, "
        f"{counts['dob_matched']} DOB account(s) updated."
    )
    return df, summary, counts


# ---- Flow 3: Haider's two responses (merge) -------------------------------

def validate_haider_ids_response(sheets: dict[str, pd.DataFrame] | None):
    _require_sheets(sheets, IDS_AND_DOB_SHEETS, missing_msg="Haider's IDs/DOB response file is required")


def apply_haider_ids_response(df: pd.DataFrame, sheets: dict[str, pd.DataFrame]):
    validate_haider_ids_response(sheets)
    counts = _apply_ids_and_dob(df, sheets)
    summary = (
        f"Applied Haider's second-pass corrections: {counts['id_matched']} ID, "
        f"{counts['dob_matched']} DOB account(s) updated."
    )
    return df, summary, counts


def validate_haider_corrections_response(sheets: dict[str, pd.DataFrame] | None):
    expected = {
        SHEET_NAME_VALIDATE: NAME_COLS,
        SHEET_MISSING_MOBILE: MOBILE_COLS,
        SHEET_CMS: CMS_UPDATE_COLS,
    }
    _require_sheets(sheets, expected, missing_msg="Haider's corrections response file is required")


def apply_haider_corrections_response(df: pd.DataFrame, sheets: dict[str, pd.DataFrame]):
    validate_haider_corrections_response(sheets)
    name_matched = _overwrite_nonblank(df, sheets[SHEET_NAME_VALIDATE], NAME_COLS)
    mobile_matched = _overwrite_nonblank(df, sheets[SHEET_MISSING_MOBILE], MOBILE_COLS)
    cms_matched = _overwrite_nonblank(df, sheets[SHEET_CMS], CMS_UPDATE_COLS)
    summary = (
        f"Applied Haider's corrections: {name_matched} name, "
        f"{mobile_matched} mobile, {cms_matched} CMS account(s) updated."
    )
    counts = {
        "name_matched": name_matched,
        "mobile_matched": mobile_matched, "cms_matched": cms_matched,
    }
    return df, summary, counts
