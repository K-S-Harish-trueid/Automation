"""Admin endpoints for the historical-override Postgres store (historical_db.py).

Lets an operator (re)seed the historical table from an xlsx/csv/parquet export
straight through the web UI, instead of someone having to run
data/seed_historical.py by hand on whatever machine the server happens to be
on. Wraps historical_db.seed_from_file -- the same validation/dedup/index
logic the CLI script uses, just fed an uploaded file instead of a path -- so
a seed done through the browser and one done via
`python backend/data/seed_historical.py` produce an identical table; there's
exactly one seeding code path either way.
"""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .. import historical_db

router = APIRouter()

_ALLOWED_SUFFIXES = {".xlsx", ".xls", ".csv", ".parquet"}


@router.get("/api/historical/status")
def historical_status():
    return historical_db.stats()


def _seed_from_upload(upload: UploadFile, suffix: str) -> int:
    # Copy to disk in 1MB pieces rather than upload.read(): a big export
    # (700MB+) would otherwise sit in RAM whole, on top of whatever the
    # parser needs, and got the app OOM-killed.
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / f"upload{suffix}"
        upload.file.seek(0)
        with open(path, "wb") as out:
            shutil.copyfileobj(upload.file, out, 1024 * 1024)
        return historical_db.seed_from_file(path, source="Dashboard")


@router.post("/api/historical/seed")
async def seed_historical(file: UploadFile = File(...)):
    """Replaces the entire historical table with the uploaded file's
    contents -- not a merge. The frontend confirms this with the operator
    before calling it. Runs off the event loop (run_in_threadpool) since
    parsing+importing a full export takes a while (see historical_db.py's
    docstring) and would otherwise stall every other request -- other jobs'
    progress polling included -- for that whole time.

    .parquet is the route for very large exports: it's streamed into
    Postgres in batches, so memory stays flat. xlsx/csv are parsed fully in
    memory first."""
    suffix = Path(file.filename or "upload.xlsx").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, "Unsupported file type. Upload a .csv, .xlsx, .xls, or .parquet file.")
    try:
        await run_in_threadpool(_seed_from_upload, file, suffix)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return historical_db.stats()
