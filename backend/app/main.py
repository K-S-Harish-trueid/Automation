import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import store
from .routes import flow, jobs, rollback, stage, stage_test


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prune leftover completed jobs from a previous run too, not just ones
    # that finish while this process is up.
    store.enforce_job_retention()
    yield


app = FastAPI(title="K2 Automation", lifespan=lifespan, debug=os.environ.get("K2_DEBUG") == "1")

# By default, allow any origin so a separately-hosted frontend (a different
# domain/port than this backend) can call the API. Restrict this once you
# know the frontend's real origin, e.g.:
#   K2_ALLOWED_ORIGINS=https://your-frontend.example.com
allowed_origins_env = os.environ.get("K2_ALLOWED_ORIGINS", "*")
allowed_origins = ["*"] if allowed_origins_env == "*" else [
    o.strip() for o in allowed_origins_env.split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(rollback.router)
app.include_router(stage.router)
app.include_router(stage_test.router)
app.include_router(flow.router)

frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
