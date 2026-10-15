import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.tasks import start_thread_scheduler
from app.models import ExportJob, GenerationJob, JobStatus, SyncRun
from app.routers import (
    admin, auth, billing, catalogs, dashboard, descriptions, developer, exports, jobs, presets, settings as org_settings,
    reports,
    sso,
    workflow,
)
from app.services import audit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()


def _fail_interrupted_jobs() -> None:
    """In thread mode, jobs die with the API process; mark them failed so they can be re-run."""
    if settings.task_mode == "celery":
        return
    active = [JobStatus.PENDING, JobStatus.RUNNING]
    with SessionLocal() as db:
        for model in (GenerationJob, ExportJob, SyncRun):
            db.execute(
                update(model).where(model.status.in_(active))
                .values(status=JobStatus.FAILED, finished_at=datetime.now(timezone.utc))
            )
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        presets.seed_builtin_presets(db)
    _fail_interrupted_jobs()
    stop = threading.Event()
    start_thread_scheduler(stop)
    yield
    stop.set()


app = FastAPI(
    title=f"{settings.app_name} API",
    description="AI product description generator for large e-commerce catalogs.",
    version="1.0.0",
    lifespan=lifespan,
    # Served under /api so the docs work through the web app's proxy (Vite in dev, nginx in Docker).
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, sso, catalogs, presets, jobs, descriptions, exports, dashboard, org_settings, billing, admin,
               developer, workflow, reports):
    app.include_router(module.router, prefix="/api")
app.include_router(presets.glossary_router, prefix="/api")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Expose the client IP and user agent to the audit log and session records."""
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    ip_token = audit.client_ip.set(ip)
    ua_token = audit.user_agent.set(request.headers.get("user-agent"))
    try:
        return await call_next(request)
    finally:
        audit.client_ip.reset(ip_token)
        audit.user_agent.reset(ua_token)


@app.get("/api/health")
def health():
    return {"status": "ok", "llm_provider": settings.llm_provider}
