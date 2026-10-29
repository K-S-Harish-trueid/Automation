import pandas as pd

from ..toolbox import NOT_COLLECTED, id_missing_mask


def default_id_invalid_mask(df: pd.DataFrame) -> pd.Series:
    """Must stay in sync with final_id_check.final_id_check_invalid_mask --
    both need to agree on exactly which rows are "missing," or this stage
    could overwrite a non-blank value Haider actually provided just because
    it doesn't match compute_id_validity's format regex."""
    return id_missing_mask(df)


def stage_default_id_assign(df: pd.DataFrame, **_):
    """No ID is generated anymore: a row still missing a usable ID after the
    final ID check gets the not-collected placeholder in both ID_TYPE and
    ID_NUMBER, so the output says plainly that no ID was collected instead
    of inventing one."""
    invalid_mask = default_id_invalid_mask(df)
    count = int(invalid_mask.sum())
    df.loc[invalid_mask, ["ID_TYPE", "ID_NUMBER"]] = NOT_COLLECTED
    return df, f"Set {count} record(s) with no usable ID to {NOT_COLLECTED}."
