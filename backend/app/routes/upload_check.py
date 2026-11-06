"""Dev-only page for trying the upload sanity check (upload_check.py) on any
file before it is wired into the real upload routes. Hidden like /test,
/delete and /roadmap. Reads the file the same way the real routes do
(store.read_table / every sheet of an .xlsx) and never stores it."""
import io
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import store
from ..upload_check import check_upload

router = APIRouter()

_PAGE_PATH = Path(__file__).resolve().parent.parent / "templates" / "upload_check.html"


@router.get("/upload-check", include_in_schema=False)
def upload_check_page():
    return FileResponse(_PAGE_PATH)


@router.post("/api/dev/upload-check", include_in_schema=False)
async def run_upload_check(file: UploadFile = File(...)):
    raw = await file.read()
    name = file.filename or "upload"
    try:
        if name.lower().endswith((".xlsx", ".xls")):
            sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, dtype=str, engine="calamine")
            sheets = {k: v.fillna("") for k, v in sheets.items()}
        else:
            sheets = {"": store.read_table(raw, name)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read {name}: {e}")
    result = check_upload(sheets)
    result["filename"] = name
    result["columns"] = {k: [str(c) for c in v.columns] for k, v in sheets.items()}
    return result
