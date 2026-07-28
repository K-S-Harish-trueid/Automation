import pandas as pd

CMS_UPDATE_COLS = ["CARD_NUMBER", "ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]

# Columns for the CMS sheet added to the review workbook download -- deliberately
# a subset of CMS_UPDATE_COLS (no CARD_NUMBER). Kept separate from CMS_UPDATE_COLS
# because the live CMS upload/merge still requires the full 5-column set.
CMS_SHEET_COLS = ["ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]


def validate_cms_reference_inputs(ref_df: pd.DataFrame):
    key = "ACCOUNT_NUMBER"
    if ref_df is None:
        raise ValueError("Reference file is required for this stage")
    missing = [c for c in [key] + CMS_UPDATE_COLS if c not in ref_df.columns]
    if missing:
        raise ValueError(f"Reference file missing columns: {missing}")


def stage_cms_integration(df: pd.DataFrame, ref_df: pd.DataFrame = None, **_):
    key = "ACCOUNT_NUMBER"
    validate_cms_reference_inputs(ref_df)

    df[key] = df[key].astype(str).str.strip()
    ref_df = ref_df.copy()
    ref_df[key] = ref_df[key].astype(str).str.strip()
    ref_df = ref_df[[key] + CMS_UPDATE_COLS].drop_duplicates(subset=[key], keep="first")

    maps = {c: dict(zip(ref_df[key], ref_df[c])) for c in CMS_UPDATE_COLS}
    matched = df[key].isin(ref_df[key])
    for c in CMS_UPDATE_COLS:
        df.loc[matched, c] = df.loc[matched, key].map(maps[c])

    return df, f"Matched {int(matched.sum())} accounts; updated CARD_NUMBER/ACCOUNT_TYPE/CARD_TYPE/CARD_PROGRAM/CARD_STATUS."
