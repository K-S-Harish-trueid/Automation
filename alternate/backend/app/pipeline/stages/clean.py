import pandas as pd


def stage_clean_linebreaks(df: pd.DataFrame, **_):
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.replace("\n", " ", regex=False).str.replace("\r", " ", regex=False)
    return df, "Removed line breaks from all fields."
