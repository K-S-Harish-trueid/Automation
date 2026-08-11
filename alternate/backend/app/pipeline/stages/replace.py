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

    # Normalise keys in both datasets (strip whitespace).
    df = df.copy()
    df[key] = df[key].astype(str).str.strip()
    ref_df = ref_df.copy()
    ref_df[key] = ref_df[key].astype(str).str.strip()

    # Keep only the columns needed from the reference file and deduplicate so
    # each ACCOUNT_NUMBER appears exactly once (last occurrence wins, matching
    # the previous sort_values + drop_duplicates(keep="last") behaviour).
    ref_slim = (
        ref_df[[key, *REPLACE_MAPPING_COLS]]
        .sort_values(by=key)
        .drop_duplicates(subset=key, keep="last")
    )

    # Single vectorised merge replaces 22 sequential .map() calls.
    # how="left" keeps every row in df; unmatched rows get NaN in the _hist
    # suffixed columns which we then ignore (only matched rows are updated).
    # indicator=True lets us count matches without a second .isin() pass.
    merged = df.merge(
        ref_slim,
        on=key,
        how="left",
        suffixes=("", "_hist"),
        indicator=True,
    )

    matched_mask = merged["_merge"] == "both"

    # Write the historical values back into df for matched rows only.
    # Non-matched rows retain their original values unchanged.
    for col in REPLACE_MAPPING_COLS:
        hist_col = f"{col}_hist"
        df.loc[matched_mask.values, col] = merged.loc[matched_mask, hist_col].values

    n_accounts = int(df.loc[matched_mask.values, key].nunique())
    n_rows = int(matched_mask.sum())
    summary = (
        f"Matched {n_accounts} accounts against the reference file; "
        f"updated {n_rows} rows across {len(REPLACE_MAPPING_COLS)} fields (CARD_NUMBER untouched)."
    )
    return df, summary

