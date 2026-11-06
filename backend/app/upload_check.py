"""Upload sanity check: catches a file that was opened and re-saved in Excel
(or otherwise mangled) before any stage uses it.

Runs on every column of every sheet, so it works for the raw K2 file and
every reply file (Naresh, Haider, both CMS exports) without knowing which
one it is.

  1. block  scientific notation in any column (2.0008E+11 -- digits already lost)
  2. block  number saved as a decimal in a number-text column (356360.0)
  3. block  ACCOUNT_NUMBER that is not digits only
  4. warn   ACCOUNT_NUMBER that lost its leading zeros: not in the historical
            store as typed, but present there with zeros in front
            (356360 -> 0000356360). No fixed length is assumed.

Blocks refuse the upload (no job is created); warnings let it through and
are shown to the operator as a notification.
"""
import logging
import re

import pandas as pd
from sqlalchemy import text

from .pipeline.toolbox import NOT_COLLECTED

logger = logging.getLogger(__name__)

# Columns that hold long digit strings Excel turns into 2.0008E+11 / 356360.0.
NUMBER_TEXT_COLS = {"ACCOUNT_NUMBER", "ID_NUMBER", "PHONE_NUMBER", "SMART_IDENTIFIER", "CARD_NUMBER"}
# Excel's exact shape: one digit, optional decimals, "E+", 1-2 digit exponent
# (2.0008E+11, 9.64751E+12, 1E+15). Real data like the ID "1186e51219" or an
# address "1006E4476672084071" must not match.
SCI = re.compile(r"-?\d(\.\d+)?E\+\d{1,2}")
FLOAT_TAIL = re.compile(r"\d+\.0+")
SKIP = {"", "0", "nan", "none", "null", "nat", NOT_COLLECTED.lower()}
EXAMPLES = 5


def _issue(col, severity, problem, mask, s, sheet, examples=None):
    rows = list(s.index[mask][:EXAMPLES])
    return {
        "sheet": sheet, "column": col, "severity": severity, "problem": problem,
        "count": int(mask.sum()), "total": int(len(s)),
        # row numbers as the user sees them in Excel (header is row 1)
        "examples": examples if examples is not None else [{"row": int(i) + 2, "value": s[i]} for i in rows],
    }


def _lost_zero_accounts(accounts: pd.Series) -> dict[str, str]:
    """{uploaded account: historical account} for uploaded account numbers
    that are not in the historical store as typed, but are there once
    leading zeros are put back (356360 -> 0000356360). Empty when the store
    is unreachable -- the check is advisory and never blocks an upload."""
    candidates = sorted({a for a in accounts if a.isdigit() and not a.startswith("0")})
    if not candidates:
        return {}
    try:
        from .historical_db import KEY, TABLE, get_engine
        sql = text(f"""
            WITH u AS (SELECT DISTINCT unnest(CAST(:accts AS text[])) AS acct)
            SELECT u.acct, h."{KEY}" AS hist
            FROM u
            JOIN "{TABLE}" h ON ltrim(h."{KEY}", '0') = u.acct
            WHERE h."{KEY}" LIKE '0%'
              AND NOT EXISTS (SELECT 1 FROM "{TABLE}" x WHERE x."{KEY}" = u.acct)
        """)
        with get_engine().connect() as conn:
            rows = conn.execute(sql, {"accts": candidates}).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        logger.exception("upload_check: lost-zero lookup against the historical store failed; skipped")
        return {}


def check_frame(df: pd.DataFrame, sheet: str = "", check_history: bool = True) -> list[dict]:
    issues = []
    for col in df.columns:
        name = str(col).strip().upper()
        s = df[col].fillna("").astype(str).str.strip()
        live = ~s.str.lower().isin(SKIP)

        # 1. Scientific notation, any column: the digits are already gone.
        m = live & s.str.fullmatch(SCI)
        if m.any():
            issues.append(_issue(col, "block", "scientific notation (digits lost, e.g. 2.0008E+11)", m, s, sheet))

        if name in NUMBER_TEXT_COLS:
            # 2. Excel number cell written back as text: 356360.0
            m = live & s.str.fullmatch(FLOAT_TAIL)
            if m.any():
                issues.append(_issue(col, "block", "number stored as decimal (e.g. 356360.0)", m, s, sheet))

        if name == "ACCOUNT_NUMBER":
            # 3. Account numbers: digits only.
            # (E+ and 356360.0 are already reported by rules 1 and 2)
            m = live & ~s.str.fullmatch(r"\d+") & ~s.str.fullmatch(SCI) & ~s.str.fullmatch(FLOAT_TAIL)
            if m.any():
                issues.append(_issue(col, "block", "account number is not digits only", m, s, sheet))
            # 4. Warning only: leading zeros lost, judged against history
            # (no fixed account length is assumed).
            if check_history:
                lost = _lost_zero_accounts(s[live])
                if lost:
                    m = live & s.isin(lost.keys())
                    rows = list(s.index[m][:EXAMPLES])
                    examples = [{"row": int(i) + 2, "value": f"{s[i]} (was {lost[s[i]]})"} for i in rows]
                    issues.append(_issue(col, "warn", "account number lost its leading zeros", m, s, sheet,
                                         examples=examples))
    return issues


def check_upload(sheets: dict[str, pd.DataFrame], check_history: bool = True) -> dict:
    """sheets: {sheet name: df} -- one entry for a CSV / single-sheet file."""
    issues = [i for name, df in sheets.items() for i in check_frame(df, name, check_history)]
    blocked = any(i["severity"] == "block" for i in issues)
    return {
        "ok": not blocked,
        "verdict": "blocked" if blocked else ("warnings" if issues else "clean"),
        "rows": {name: int(len(df)) for name, df in sheets.items()},
        "issues": issues,
    }


class UploadBlocked(ValueError):
    """Raised for a file that fails a blocking rule. `result` is the full
    check_upload() result so the route can return it to the UI."""

    def __init__(self, filename: str, result: dict):
        self.result = {**result, "filename": filename}
        blocks = [i for i in result["issues"] if i["severity"] == "block"]
        parts = [f"{i['column']}: {i['count']:,} {i['problem']}" for i in blocks[:4]]
        super().__init__(
            f"{filename} looks damaged (opened and saved in Excel?) and was not used. "
            + "; ".join(parts) + ". Upload the original export."
        )


def enforce(sheets: dict[str, pd.DataFrame], filename: str) -> list[dict]:
    """Run the check on an uploaded file: raises UploadBlocked on a blocking
    rule, otherwise returns the warnings (each tagged with the file name)."""
    result = check_upload(sheets)
    if not result["ok"]:
        raise UploadBlocked(filename, result)
    return [{**i, "filename": filename} for i in result["issues"]]
