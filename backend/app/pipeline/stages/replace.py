import pandas as pd

REPLACE_MAPPING_COLS = [
    "ACCOUNT_LAST_NAME", "ACCOUNT_FIRST_NAME", "ACCOUNT_MIDDLE_NAME", "DATE_OPENED",
    "ACCOUNT_HOLDER_DOB", "ACCOUNT_TYPE", "CARD_TYPE", "ACCOUNT_ADDRESS", "ADDRESS_CITY",
    "ADDRESS_PROVINCE", "ADDRESS_COUNTRY", "POSTAL_CODE", "PHONE_NUMBER", "EMAIL_ADDRESS",
    "ID_TYPE", "ID_NUMBER", "ID_COUNTRY", "NATIONALITY", "ISSUING_FI", "CARD_PROGRAM",
    "CARD_STATUS", "SECONDARY_CARD_TYPE",
]


def validate_replace_reference_inputs(df: pd.DataFrame, ref_df: pd.DataFrame):
    key = "ACCOUNT_NUMBER"
    if ref_df is None:
        raise ValueError("Reference file is required for this stage")
    if key not in df.columns or key not in ref_df.columns:
        raise ValueError(f"Both files must contain '{key}'")
    missing_base = [c for c in REPLACE_MAPPING_COLS if c not in df.columns]
    missing_ref = [c for c in REPLACE_MAPPING_COLS if c not in ref_df.columns]
    if missing_base:
        raise ValueError(f"Base file missing replacement columns: {missing_base}")
    if missing_ref:
        raise ValueError(f"Reference file missing replacement columns: {missing_ref}")


def stage_replace_reference(df: pd.DataFrame, ref_df: pd.DataFrame = None, **_):
    key = "ACCOUNT_NUMBER"
    validate_replace_reference_inputs(df, ref_df)
    df[key] = df[key].astype(str).str.strip()
    ref_df = ref_df.copy()
    ref_df[key] = ref_df[key].astype(str).str.strip()
    ref_df = ref_df.sort_values(by=[key]).drop_duplicates(subset=[key], keep="last")
    ref_indexed = ref_df.set_index(key)

    mask_match = df[key].isin(ref_indexed.index)
    for col in REPLACE_MAPPING_COLS:
        df.loc[mask_match, col] = df.loc[mask_match, key].map(ref_indexed[col])

    summary = (
        f"Matched {int(df.loc[mask_match, key].nunique())} accounts against the reference file; "
        f"updated {int(mask_match.sum())} rows across {len(REPLACE_MAPPING_COLS)} fields (CARD_NUMBER untouched)."
    )
    return df, summary
