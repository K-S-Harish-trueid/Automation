"""Standalone page for (re)loading address_fix's denylist/pool tables from
an xlsx export, without shell access to the server. Same no-auth stance as
stage_test.py (see backend/README.md's security note) and deliberately not
linked from the main app UI -- reachable only by knowing the /seed URL.

Mirrors routes/historical.py's pattern exactly (status endpoint + upload
endpoint wrapping a *_db.py seed_from_bytes) so the two admin seed flows
don't diverge in shape. Exists because a fresh install's address_pool table
starts empty (see app.address_pools_db's module docstring), which makes
every "unmapped province" row in address_fix's auto-fix preview look broken
until someone seeds it."""
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import address_pools_db

router = APIRouter()

_PAGE_PATH = Path(__file__).resolve().parent.parent / "templates" / "seed.html"


@router.get("/seed", include_in_schema=False)
def seed_page():
    return FileResponse(_PAGE_PATH)


@router.get("/api/seed/status", include_in_schema=False)
def seed_status():
    return address_pools_db.stats()


@router.post("/api/seed/address-pools", include_in_schema=False)
async def seed_address_pools(file: UploadFile = File(...)):
    """Replaces address_denylist/address_pool wholesale with the uploaded
    xlsx's contents (two sheets, named after the tables -- see
    address_pools_db.export_to_xlsx for producing that file from a live
    table, and seed_from_bytes for the exact format expected back)."""
    raw = await file.read()
    try:
        address_pools_db.seed_from_bytes(raw, file.filename or "upload.xlsx")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return address_pools_db.stats()
