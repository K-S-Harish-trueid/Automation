import random

import pandas as pd

from .utils import _s
from .validators import compute_id_validity


def stage_clean_linebreaks(df: pd.DataFrame, **_):
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.replace("\n", " ", regex=False).str.replace("\r", " ", regex=False)
    return df, "Removed line breaks from all fields."


def stage_reset_cms_fields(df: pd.DataFrame, **_):
    for col in ["ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]:
        if col in df.columns:
            df[col] = ""
    return df, "Cleared ACCOUNT_TYPE, CARD_TYPE, CARD_PROGRAM, CARD_STATUS (to be repopulated from CMS)."


def default_id_invalid_mask(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    return ~(type_valid & num_valid)


def stage_default_id_assign(df: pd.DataFrame, **_):
    invalid_mask = default_id_invalid_mask(df)
    # Avoid every existing value, including IDs on rows that currently fail
    # validation, so generated Civil IDs cannot duplicate data already present.
    used = set(_s(df, "ID_NUMBER"))
    rnd = random.Random()
    new_ids = []
    for _ in range(int(invalid_mask.sum())):
        while True:
            candidate = "00" + f"{rnd.randint(0, 999999):06d}"
            if candidate not in used:
                used.add(candidate)
                new_ids.append(candidate)
                break
    df.loc[invalid_mask, "ID_NUMBER"] = new_ids
    df.loc[invalid_mask, "ID_TYPE"] = "Civil Id"
    return df, f"Assigned default Civil ID numbers to {int(invalid_mask.sum())} remaining invalid records."
