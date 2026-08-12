"""Admin endpoints for the historical-override SQLite store (historical_db.py).

Lets an operator (re)seed historical.db from an xlsx/csv export straight
through the web UI, instead of someone having to run data/seed_historical.py
by hand on whatever machine the server happens to be on. Wraps
historical_db.seed_from_bytes -- the same validation/dedup/index logic
seed_from_file (the CLI script's entry point) uses, just fed bytes already in
hand instead of a file path -- so a seed done through the browser and one
done via `python backend/data/seed_historical.py` produce an identical
table; there's exactly one seeding code path either way.
"""
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .. import historical_db

router = APIRouter()


@router.get("/api/historical/status")
def historical_status():
    return historical_db.stats()


@router.post("/api/historical/seed")
async def seed_historical(file: UploadFile = File(...)):
    """Replaces the entire historical table with the uploaded file's
    contents (historical_db.seed_from_bytes's existing if_exists='replace'
    behavior) -- not a merge. The frontend confirms this with the operator
    before calling it. Runs off the event loop (run_in_threadpool) since
    parsing+importing a full export takes tens of seconds (see
    historical_db.py's docstring) and would otherwise stall every other
    request -- other jobs' progress polling included -- for that whole time."""
    raw = await file.read()
    try:
        await run_in_threadpool(historical_db.seed_from_bytes, raw, file.filename or "upload.xlsx")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return historical_db.stats()
