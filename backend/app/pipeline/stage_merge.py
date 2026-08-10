"""Apply/validate logic for the Stage 1/2/3 reviewer handoff (Naresh does IDs
+ DOB first, Haider does Name plus a second pass on whatever IDs and DOBs
Naresh couldn't resolve; CMS mobile/card data comes from two direct CMS
system exports, not from Haider by hand). Pure pandas -- no FastAPI here,
see routes/handoff.py for the HTTP glue and helpers.py for the matching
xlsx writers.

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
SHEET_DOB_CORRECTIONS = "DOB Corrections"
SHEET_MISSING_MOBILE = "Missing Mobile Numbers"
# Blank-CMS-sheet name used only by the Stage 1 Haider dispatch file as a
# starting point -- real CMS mobile/card data comes in at Stage 3 via the two
# direct CMS exports (CMS_MOBILE_COLS/CMS_CARD_COLS below), not from anything
# read back out of this sheet.
SHEET_CMS = "CMS Data Integration"

# Shared by Naresh's Stage 2 response and Haider's Stage 3 second-pass
# response -- both are the exact same 2-sheet shape (IDs + DOB), just
# submitted by a different reviewer at a different point in the handoff.
IDS_AND_DOB_SHEETS = {SHEET_ID_CORRECTIONS: ID_COLS, SHEET_DOB_CORRECTIONS: DOB_COLS}


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
    dob_matched = _overwrite_nonblank(df, sheets[SHEET_DOB_CORRECTIONS], DOB_COLS)
    return {"id_matched": id_matched, "dob_matched": dob_matched}


# ---- Stage 2: Naresh's ID + DOB response (merge) ---------------------------

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


# ---- Stage 3: three-file handoff (CMS Mobile, CMS Card, Haider's corrections) ----

# CMS Mobile Numbers export: ACCOUNT_NUMBER + PHONE_NUMBER (flat table, no sheets)
CMS_MOBILE_COLS = MOBILE_COLS  # ["PHONE_NUMBER"]

# CMS Card Details export: ACCOUNT_NUMBER + CMS_UPDATE_COLS (flat table, no sheets)
# DATE_OPENED arrives as M/D/YYYY from the CMS export and must be normalised to
# YYYY-MM-DD before being written into the main dataset.
CMS_CARD_COLS = CMS_UPDATE_COLS  # ["CARD_NUMBER", "ACCOUNT_TYPE", ..., "DATE_OPENED"]

# Haider's corrected file: three-sheet workbook (Name/ID/DOB -- no CMS or
# Mobile, those come from the two CMS exports above instead).
HAIDER_CORRECTIONS_SHEETS = {
    SHEET_NAME_VALIDATE: NAME_COLS,
    SHEET_ID_CORRECTIONS: ID_COLS,
    SHEET_DOB_CORRECTIONS: DOB_COLS,
}


def _normalise_date_opened(resp: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of resp with DATE_OPENED values normalised to YYYY-MM-DD.
    The CMS card export uses M/D/YYYY (e.g. '4/23/2024'). The main dataset
    stores YYYY-MM-DD, so we parse whatever format arrives and reformat it.
    Blank or unparseable values are left as-is so _overwrite_nonblank skips them."""
    if "DATE_OPENED" not in resp.columns:
        return resp
    resp = resp.copy()
    raw = resp["DATE_OPENED"].astype(str).str.strip()
    parsed = pd.to_datetime(raw, errors="coerce")
    valid = parsed.notna()
    resp.loc[valid, "DATE_OPENED"] = parsed[valid].dt.strftime("%Y-%m-%d")
    return resp


def validate_cms_mobile_response(df_mobile: pd.DataFrame | None):
    """Validate the flat CMS Mobile Numbers dataframe (not a workbook)."""
    if df_mobile is None or df_mobile.empty:
        raise ValueError("CMS Mobile Numbers file is required and must not be empty")
    _require_columns(df_mobile, CMS_MOBILE_COLS)


def apply_cms_mobile_response(df: pd.DataFrame, df_mobile: pd.DataFrame):
    """Merge PHONE_NUMBER from the CMS Mobile Numbers file into df.
    Accounts absent from the CMS file (or left with a blank/zero number
    after the merge) are explicitly marked as NOT_COLLECTED so the rest of
    the pipeline treats them consistently."""
    from .toolbox import NOT_COLLECTED
    validate_cms_mobile_response(df_mobile)
    mobile_matched = _overwrite_nonblank(df, df_mobile, CMS_MOBILE_COLS)
    phone = df["PHONE_NUMBER"].astype(str).str.strip()
    missing_mask = phone.eq("") | phone.str.fullmatch(r"0+", na=False)
    df.loc[missing_mask, "PHONE_NUMBER"] = NOT_COLLECTED
    not_collected_count = int(missing_mask.sum())
    summary = (
        f"Applied CMS Mobile Numbers: {mobile_matched} account(s) updated, "
        f"{not_collected_count} account(s) marked as {NOT_COLLECTED}."
    )
    return df, summary, {"mobile_matched": mobile_matched, "not_collected": not_collected_count}


def validate_cms_card_response(df_card: pd.DataFrame | None):
    """Validate the flat CMS Card Details dataframe (not a workbook)."""
    if df_card is None or df_card.empty:
        raise ValueError("CMS Card Details file is required and must not be empty")
    _require_columns(df_card, CMS_CARD_COLS)


def apply_cms_card_response(df: pd.DataFrame, df_card: pd.DataFrame):
    """Normalise DATE_OPENED then merge all CMS card columns into df."""
    validate_cms_card_response(df_card)
    df_card_norm = _normalise_date_opened(df_card)
    cms_matched = _overwrite_nonblank(df, df_card_norm, CMS_CARD_COLS)
    summary = f"Applied CMS Card Details: {cms_matched} account(s) updated."
    return df, summary, {"cms_matched": cms_matched}


def validate_haider_corrections_response(sheets: dict[str, pd.DataFrame] | None):
    """Validate Haider's corrected workbook (3-sheet: Name/ID/DOB)."""
    _require_sheets(sheets, HAIDER_CORRECTIONS_SHEETS,
                    missing_msg="Haider's corrected file is required")


def apply_haider_corrections_response(df: pd.DataFrame, sheets: dict[str, pd.DataFrame]):
    """Merge Name Validation, ID Corrections, and DOB Corrections sheets from
    Haider's corrected workbook into df."""
    validate_haider_corrections_response(sheets)
    name_matched = _overwrite_nonblank(df, sheets[SHEET_NAME_VALIDATE], NAME_COLS)
    id_matched = _overwrite_nonblank(df, sheets[SHEET_ID_CORRECTIONS], ID_COLS)
    dob_matched = _overwrite_nonblank(df, sheets[SHEET_DOB_CORRECTIONS], DOB_COLS)
    summary = (
        f"Applied Haider's corrections: {name_matched} name, "
        f"{id_matched} ID, {dob_matched} DOB account(s) updated."
    )
    counts = {"name_matched": name_matched, "id_matched": id_matched, "dob_matched": dob_matched}
    return df, summary, counts
