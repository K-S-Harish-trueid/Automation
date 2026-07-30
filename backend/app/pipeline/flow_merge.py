"""Apply/validate logic for the Flow 1/2/3 reviewer handoff (Naresh does IDs
first, Haider does Name/DOB/Mobile/CMS plus a second pass on whatever IDs
Naresh couldn't resolve). Pure pandas -- no FastAPI here, see routes/flow.py
for the HTTP glue and helpers.py for the matching xlsx writers.

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

SHEET_NAME_VALIDATE = "Name Validation"
SHEET_DOB_MISTAKES = "DOB Mistakes"
SHEET_MISSING_MOBILE = "Missing Mobile Numbers"
SHEET_CMS = "CMS Data Integration"


def _require_columns(df: pd.DataFrame, columns: list[str], *, sheet: str | None = None):
    missing = [c for c in [KEY, *columns] if c not in df.columns]
    if missing:
        where = f" in the '{sheet}' sheet" if sheet else ""
        raise ValueError(f"Uploaded file is missing columns{where}: {missing}")


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


# ---- Flow 2: Naresh's ID response (merge) --------------------------------

def validate_naresh_response(resp_df: pd.DataFrame | None):
    if resp_df is None:
        raise ValueError("Naresh's response file is required")
    _require_columns(resp_df, ID_COLS)


def apply_naresh_response(df: pd.DataFrame, resp_df: pd.DataFrame):
    validate_naresh_response(resp_df)
    matched = _overwrite_nonblank(df, resp_df, ID_COLS)
    return df, f"Applied Naresh's ID corrections to {matched} account(s).", matched


# ---- Flow 3: Haider's two responses (merge) -------------------------------

def validate_haider_ids_response(resp_df: pd.DataFrame | None):
    if resp_df is None:
        raise ValueError("Haider's IDs response file is required")
    _require_columns(resp_df, ID_COLS)


def apply_haider_ids_response(df: pd.DataFrame, resp_df: pd.DataFrame):
    validate_haider_ids_response(resp_df)
    matched = _overwrite_nonblank(df, resp_df, ID_COLS)
    return df, f"Applied Haider's ID corrections to {matched} account(s).", matched


def validate_haider_corrections_response(sheets: dict[str, pd.DataFrame] | None):
    if not sheets:
        raise ValueError("Haider's corrections response file is required")
    expected = {
        SHEET_NAME_VALIDATE: NAME_COLS,
        SHEET_DOB_MISTAKES: DOB_COLS,
        SHEET_MISSING_MOBILE: MOBILE_COLS,
        SHEET_CMS: CMS_UPDATE_COLS,
    }
    missing_sheets = [name for name in expected if name not in sheets]
    if missing_sheets:
        raise ValueError(f"Uploaded workbook is missing sheet(s): {missing_sheets}")
    for name, columns in expected.items():
        _require_columns(sheets[name], columns, sheet=name)


def apply_haider_corrections_response(df: pd.DataFrame, sheets: dict[str, pd.DataFrame]):
    validate_haider_corrections_response(sheets)
    name_matched = _overwrite_nonblank(df, sheets[SHEET_NAME_VALIDATE], NAME_COLS)
    dob_matched = _overwrite_nonblank(df, sheets[SHEET_DOB_MISTAKES], DOB_COLS)
    mobile_matched = _overwrite_nonblank(df, sheets[SHEET_MISSING_MOBILE], MOBILE_COLS)
    cms_matched = _overwrite_nonblank(df, sheets[SHEET_CMS], CMS_UPDATE_COLS)
    summary = (
        f"Applied Haider's corrections: {name_matched} name, {dob_matched} DOB, "
        f"{mobile_matched} mobile, {cms_matched} CMS account(s) updated."
    )
    counts = {
        "name_matched": name_matched, "dob_matched": dob_matched,
        "mobile_matched": mobile_matched, "cms_matched": cms_matched,
    }
    return df, summary, counts
