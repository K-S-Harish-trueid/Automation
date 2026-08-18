import random

import pandas as pd

from ...rules_config import GENERATED_ID_PREFIX, GENERATED_ID_RANDOM_DIGITS
from ..toolbox import _s, id_missing_mask


def default_id_invalid_mask(df: pd.DataFrame) -> pd.Series:
    """Must stay in sync with final_id_check.final_id_check_invalid_mask --
    both need to agree on exactly which rows are "missing," or this stage
    could overwrite a non-blank value Haider actually provided just because
    it doesn't match compute_id_validity's format regex."""
    return id_missing_mask(df)


def stage_default_id_assign(df: pd.DataFrame, **_):
    invalid_mask = default_id_invalid_mask(df)
    # Avoid every existing value, including IDs on rows that currently fail
    # validation, so generated Civil IDs cannot duplicate data already present.
    used = set(_s(df, "ID_NUMBER"))
    rnd = random.Random()
    new_ids = []
    max_value = 10 ** GENERATED_ID_RANDOM_DIGITS - 1
    for _ in range(int(invalid_mask.sum())):
        while True:
            candidate = GENERATED_ID_PREFIX + f"{rnd.randint(0, max_value):0{GENERATED_ID_RANDOM_DIGITS}d}"
            if candidate not in used:
                used.add(candidate)
                new_ids.append(candidate)
                break
    df.loc[invalid_mask, "ID_NUMBER"] = new_ids
    df.loc[invalid_mask, "ID_TYPE"] = "Civil Id"
    return df, f"Assigned default Civil ID numbers to {int(invalid_mask.sum())} remaining invalid records."
