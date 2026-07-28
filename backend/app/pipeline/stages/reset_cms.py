import pandas as pd


def stage_reset_cms_fields(df: pd.DataFrame, **_):
    for col in ["ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]:
        if col in df.columns:
            df[col] = ""
    return df, "Cleared ACCOUNT_TYPE, CARD_TYPE, CARD_PROGRAM, CARD_STATUS (to be repopulated from CMS)."
