from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import can_edit, get_current_user, get_owned
from app.models import Catalog, GenerationJob, JobStatus, Organization, Product, User
from app.routers.presets import get_usable_preset
from app.schemas import JobCreate, JobEstimate, JobOut
from app.services.generation import estimate_job, plan_tasks, remaining_quota
from app.services.llm import available_engines, default_engine
from app.services import audit
from app.tasks import dispatch_generation

router = APIRouter(prefix="/jobs", tags=["generation"])


def check_engine(engine: str | None, org: Organization) -> None:
    """The engine must be usable by this organization (installed/configured and allowed by its plan)."""
    engine = engine or default_engine()
    match = next((e for e in available_engines(org) if e["id"] == engine), None)
    if match is None or not match["available"]:
        reason = match["note"] if match else "unknown engine"
        code = status.HTTP_402_PAYMENT_REQUIRED if match and match["needs_upgrade"] else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, f"AI engine '{engine}' is not available: {reason}")


def _resolve_tasks(db: Session, body: JobCreate, user: User) -> list[tuple[int, str]]:
    get_owned(db, Catalog, body.catalog_id, user)
    check_engine(body.engine, db.get(Organization, user.org_id))
    query = select(Product.id).where(Product.catalog_id == body.catalog_id)
    if body.product_ids:
        query = query.where(Product.id.in_(body.product_ids))
    product_ids = list(db.scalars(query))
    return plan_tasks(db, product_ids, body.languages, body.only_missing)


def _images_per_item(db: Session, tasks: list[tuple[int, str]], body: JobCreate) -> tuple[int, float]:
    """(products with images, average images sent per request) for the estimate."""
    if not body.use_images or not tasks:
        return 0, 0.0
    ids = {pid for pid, _ in tasks}
    counts = [min(3, len(imgs or [])) for imgs in db.scalars(select(Product.images).where(Product.id.in_(ids)))]
    with_images = sum(1 for n in counts if n)
    return with_images, (sum(counts) / len(counts) if counts else 0.0)


@router.post("/estimate", response_model=JobEstimate)
def estimate(body: JobCreate, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    tasks = _resolve_tasks(db, body, user)
    org = db.get(Organization, user.org_id)
    with_images, per_item = _images_per_item(db, tasks, body)
    return JobEstimate(
        **estimate_job(len(tasks) * body.variants, body.engine, per_item),
        products_with_images=with_images,
        remaining_quota=remaining_quota(db, org),
    )


@router.post("", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    get_usable_preset(db, body.preset_id, user)
    tasks = _resolve_tasks(db, body, user)
    if not tasks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to generate for this selection")
    org = db.get(Organization, user.org_id)
    remaining = remaining_quota(db, org)
    needed = len(tasks) * body.variants
    if needed > remaining:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"This job needs {needed} generations but only {remaining} remain in this month's plan",
        )
    running = db.scalar(
        select(GenerationJob.id).where(
            GenerationJob.catalog_id == body.catalog_id,
            GenerationJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
    )
    if running:
        raise HTTPException(status.HTTP_409_CONFLICT, "A generation job is already running for this catalog")

    job = GenerationJob(
        org_id=user.org_id,
        catalog_id=body.catalog_id,
        preset_id=body.preset_id,
        engine=body.engine or default_engine(),
        languages=body.languages,
        product_ids=body.product_ids,
        only_missing=body.only_missing,
        extra_instructions=body.extra_instructions,
        variants=body.variants,
        use_images=body.use_images,
        total=needed,
        created_by=user.id,
    )
    db.add(job)
    db.commit()
    audit.record(db, user, "job.created", target_type="job", target_id=job.id, details={
        "catalog_id": job.catalog_id, "items": needed, "engine": job.engine, "languages": job.languages})
    dispatch_generation(job.id)
    db.refresh(job)
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(
    catalog_id: int | None = None,
    limit: int = Query(20, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(GenerationJob).where(GenerationJob.org_id == user.org_id)
    if catalog_id:
        query = query.where(GenerationJob.catalog_id == catalog_id)
    return db.scalars(query.order_by(GenerationJob.id.desc()).limit(limit)).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned(db, GenerationJob, job_id, user)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    job = get_owned(db, GenerationJob, job_id, user)
    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        job.status = JobStatus.CANCELLED
        db.commit()
        audit.record(db, user, "job.cancelled", target_type="job", target_id=job.id)
    return job
