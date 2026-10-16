#!/usr/bin/env python
"""Convert a big historical xlsx export into a small Parquet file for seeding.

Why: a ~700MB xlsx can't be parsed on the 4GB server (it loads the whole
table into RAM and gets OOM-killed). Run this on a machine with plenty of
RAM instead, then upload the .parquet through the Historical reference data
screen (or `seed_historical.py --source file.parquet`). The server streams
Parquet in small batches, so memory stays flat.

Keeps only the columns the historical store actually uses (ACCOUNT_NUMBER +
REPLACE_MAPPING_COLS) -- everything else in the xlsx is dropped by the seed
anyway -- and writes every value as text, matching what the xlsx/csv seed
path produces.

Reads the sheet row-by-row (python-calamine's iter_rows) rather than
pd.read_excel, so it never builds the whole table as Python objects.

Usage:
    python backend/data/xlsx_to_parquet.py "D:\\path\\to\\export.xlsx"
    python backend/data/xlsx_to_parquet.py in.xlsx --out out.parquet --sheet Sheet1
"""
import argparse
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from python_calamine import CalamineWorkbook

BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/data/ -> backend/
sys.path.insert(0, str(BACKEND_DIR))

# Import via app.pipeline, not app.historical_db directly: importing
# historical_db first trips a circular import (address_pools_db imports it
# back); the app itself always loads app.pipeline first.
from app.pipeline.stages.replace import REPLACE_MAPPING_COLS  # noqa: E402

COLUMNS = ["ACCOUNT_NUMBER", *REPLACE_MAPPING_COLS]

CHUNK_ROWS = 100_000


def _cell_to_text(value) -> str:
    # Mirrors pd.read_excel(dtype=str).fillna(""): empty -> "", whole-number
    # floats (calamine hands numeric cells back as float) lose the ".0".
    if value is None or value == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def convert(src: Path, out: Path, sheet: str | None) -> int:
    wb = CalamineWorkbook.from_path(str(src))
    ws = wb.get_sheet_by_name(sheet) if sheet else wb.get_sheet_by_index(0)
    rows = ws.iter_rows()

    header = [str(c).replace("\ufeff", "").strip() for c in next(rows)]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise SystemExit(f"Missing columns in {src.name}: {missing}")
    idx = [header.index(c) for c in COLUMNS]

    schema = pa.schema([(c, pa.string()) for c in COLUMNS])
    total = 0
    started = time.time()
    with pq.ParquetWriter(out, schema, compression="zstd") as writer:
        buf: list[list[str]] = [[] for _ in COLUMNS]

        def flush():
            nonlocal total
            if not buf[0]:
                return
            writer.write_table(pa.Table.from_arrays([pa.array(col, pa.string()) for col in buf], schema=schema))
            total += len(buf[0])
            for col in buf:
                col.clear()
            print(f"  {total:,} rows  ({time.time() - started:.0f}s)", flush=True)

        for row in rows:
            for out_col, i in zip(buf, idx):
                out_col.append(_cell_to_text(row[i]) if i < len(row) else "")
            if len(buf[0]) >= CHUNK_ROWS:
                flush()
        flush()
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="the .xlsx export")
    ap.add_argument("--out", type=Path, help="output .parquet (default: next to the source)")
    ap.add_argument("--sheet", help="sheet name (default: first sheet)")
    args = ap.parse_args()

    out = args.out or args.source.with_suffix(".parquet")
    print(f"Converting {args.source} -> {out}")
    rows = convert(args.source, out, args.sheet)
    print(f"Done: {rows:,} rows, {out.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
