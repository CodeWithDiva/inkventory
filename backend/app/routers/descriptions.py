"""Review & approval workflow, manual edits, version history and regeneration."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import can_edit, can_review, get_current_user, get_owned
from app.models import Catalog, DescriptionVersion, Organization, Product, ReviewStatus, Role, User
from app.routers.jobs import check_engine
from app.routers.presets import get_usable_preset
from app.schemas import ApproveDraftsIn, BulkReviewIn, RegenerateIn, ReviewIn, VersionEdit, VersionOut
from app.services import audit, notifications, webhooks
from app.services.generation import generate_single, remaining_quota, save_version
from app.services.llm import GeneratedContent, LLMError

router = APIRouter(tags=["descriptions"])


@router.get("/products/{product_id}/versions", response_model=list[VersionOut])
def list_versions(
    product_id: int,
    language: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Full version history for a product (newest first)."""
    get_owned(db, Product, product_id, user)
    query = select(DescriptionVersion).where(DescriptionVersion.product_id == product_id)
    if language:
        query = query.where(DescriptionVersion.language == language)
    return db.scalars(query.order_by(DescriptionVersion.language, DescriptionVersion.version_no.desc())).all()


@router.get("/descriptions/{version_id}", response_model=VersionOut)
def get_version(version_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned(db, DescriptionVersion, version_id, user)


@router.post("/products/{product_id}/regenerate", response_model=VersionOut, status_code=201)
def regenerate(product_id: int, body: RegenerateIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    product = get_owned(db, Product, product_id, user)
    preset_id = body.preset_id
    if preset_id is None:
        # Keep the style of the version being replaced.
        preset_id = db.scalar(
            select(DescriptionVersion.preset_id).where(
                DescriptionVersion.product_id == product.id,
                DescriptionVersion.language == body.language,
                DescriptionVersion.is_current.is_(True),
            )
        )
    preset = get_usable_preset(db, preset_id, user)
    org = db.get(Organization, user.org_id)
    check_engine(body.engine, org)
    if remaining_quota(db, org) < 1:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Monthly generation limit reached")
    try:
        return generate_single(
            db, product, body.language, preset, extra_instructions=body.extra_instructions, user_id=user.id,
            engine=body.engine, org=org, use_images=body.use_images,
        )
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.put("/descriptions/{version_id}", response_model=VersionOut, status_code=201)
def edit_version(version_id: int, body: VersionEdit, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    """Manual edits never overwrite history: they create a new version based on this one."""
    base = get_owned(db, DescriptionVersion, version_id, user)
    product = db.get(Product, base.product_id)
    version = save_version(
        db, product, base.language, GeneratedContent(**body.model_dump()),
        source="manual", preset_id=base.preset_id, user_id=user.id,
    )
    db.commit()
    return version


@router.post("/descriptions/{version_id}/make-current", response_model=VersionOut)
def make_current(version_id: int, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    """Pick this version (e.g. one of several AI variants) as the one under review, without copying it."""
    version = get_owned(db, DescriptionVersion, version_id, user)
    db.execute(
        update(DescriptionVersion)
        .where(DescriptionVersion.product_id == version.product_id, DescriptionVersion.language == version.language)
        .values(is_current=False)
    )
    version.is_current = True
    db.commit()
    db.refresh(version)
    return version


@router.post("/descriptions/{version_id}/restore", response_model=VersionOut, status_code=201)
def restore_version(version_id: int, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    """Make an older version current again (as a new version, so history stays linear)."""
    old = get_owned(db, DescriptionVersion, version_id, user)
    product = db.get(Product, old.product_id)
    content = GeneratedContent(
        title=old.title, short_description=old.short_description, long_description=old.long_description,
        bullet_points=old.bullet_points, meta_title=old.meta_title, meta_description=old.meta_description,
    )
    version = save_version(db, product, old.language, content, source="restore",
                           preset_id=old.preset_id, user_id=user.id, model=old.model)
    db.commit()
    return version


def _is_admin(user: User) -> bool:
    return user.role in (Role.OWNER, Role.ADMIN)


def _approve(version: DescriptionVersion, org: Organization, user: User, note: str | None) -> ReviewStatus | None:
    """Apply one approval. Returns the new status, or None when this user may not approve it now.

    One-step workflow: any reviewer approves. Two-step: a reviewer's approval moves the draft to
    "in_review"; an admin gives the final approval (admins may also approve a draft directly).
    """
    now = datetime.now(timezone.utc)
    if org.approval_steps >= 2 and not _is_admin(user):
        if version.status == ReviewStatus.IN_REVIEW:
            return None  # waiting for an admin
        version.status = ReviewStatus.IN_REVIEW
        version.first_approved_by, version.first_approved_at = user.id, now
    else:
        version.status = ReviewStatus.APPROVED
    version.review_note = note
    version.reviewed_by = user.id
    version.reviewed_at = now
    return version.status


def _reject(version: DescriptionVersion, user: User, note: str | None) -> None:
    version.status = ReviewStatus.REJECTED
    version.review_note = note
    version.reviewed_by = user.id
    version.reviewed_at = datetime.now(timezone.utc)


def _review_events(db: Session, user: User, versions: list[DescriptionVersion], new_status: ReviewStatus) -> None:
    if not versions:
        return
    if new_status == ReviewStatus.IN_REVIEW:
        audit.record(db, user, "description.approved", details={
            "count": len(versions), "step": "first approval", "version_ids": [v.id for v in versions[:50]]})
        link = _product_link(db, versions[0])
        notifications.notify(
            db, user.org_id, notifications.admin_ids(db, user.org_id), "approval_needed",
            f"{len(versions)} description(s) need final approval",
            f"{user.full_name} approved them; an admin needs to give final approval.", link, exclude=user.id)
        return
    event = "description.approved" if new_status == ReviewStatus.APPROVED else "description.rejected"
    audit.record(db, user, event, details={"count": len(versions), "version_ids": [v.id for v in versions[:50]]})
    items = [{"version_id": v.id, "product_id": v.product_id, "language": v.language} for v in versions[:500]]
    webhooks.safe_emit(db, user.org_id, event, {"count": len(versions), "items": items})
    if new_status == ReviewStatus.REJECTED:
        for v in versions[:100]:
            product = db.get(Product, v.product_id)
            recipients = {v.created_by, product.assignee_id if product else None}
            notifications.notify(
                db, user.org_id, [r for r in recipients if r], "rejected",
                f"Description rejected: {product.name if product else v.product_id}",
                v.review_note or "", _product_link(db, v), exclude=user.id, commit=False)
        db.commit()


def _product_link(db: Session, version: DescriptionVersion) -> str:
    product = db.get(Product, version.product_id)
    return f"/catalogs/{product.catalog_id}/products/{product.id}?lang={version.language}" if product else "/"


@router.post("/descriptions/{version_id}/approve", response_model=VersionOut)
def approve(version_id: int, body: ReviewIn | None = None, user: User = Depends(can_review), db: Session = Depends(get_db)):
    version = get_owned(db, DescriptionVersion, version_id, user)
    status_after = _approve(version, db.get(Organization, user.org_id), user, body.note if body else None)
    if status_after is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This description is waiting for final approval by an admin")
    db.commit()
    _review_events(db, user, [version], status_after)
    return version


@router.post("/descriptions/{version_id}/reject", response_model=VersionOut)
def reject(version_id: int, body: ReviewIn | None = None, user: User = Depends(can_review), db: Session = Depends(get_db)):
    version = get_owned(db, DescriptionVersion, version_id, user)
    _reject(version, user, body.note if body else None)
    db.commit()
    _review_events(db, user, [version], ReviewStatus.REJECTED)
    return version


def _approve_many(db: Session, user: User, versions: list[DescriptionVersion], note: str | None) -> dict:
    org = db.get(Organization, user.org_id)
    by_status: dict[ReviewStatus, list[DescriptionVersion]] = {}
    skipped = 0
    for version in versions:
        result = _approve(version, org, user, note)
        if result is None:
            skipped += 1
        else:
            by_status.setdefault(result, []).append(version)
    db.commit()
    for new_status, group in by_status.items():
        _review_events(db, user, group, new_status)
    updated = sum(len(g) for g in by_status.values())
    return {"updated": updated, "skipped": skipped,
            "awaiting_final_approval": len(by_status.get(ReviewStatus.IN_REVIEW, []))}


@router.post("/descriptions/bulk-review")
def bulk_review(body: BulkReviewIn, user: User = Depends(can_review), db: Session = Depends(get_db)):
    versions = list(db.scalars(
        select(DescriptionVersion).where(
            DescriptionVersion.id.in_(body.version_ids), DescriptionVersion.org_id == user.org_id
        )
    ).all())
    if body.action == "approve":
        return _approve_many(db, user, versions, body.note)
    for version in versions:
        _reject(version, user, body.note)
    db.commit()
    _review_events(db, user, versions, ReviewStatus.REJECTED)
    return {"updated": len(versions), "skipped": 0, "awaiting_final_approval": 0}


@router.post("/catalogs/{catalog_id}/approve-drafts")
def approve_drafts(
    catalog_id: int,
    body: ApproveDraftsIn,
    user: User = Depends(can_review),
    db: Session = Depends(get_db),
):
    """Approve all current drafts in a language that meet a minimum SEO score (optionally skipping flagged ones).

    In the two-step workflow, admins can also use this to give final approval to everything "in review".
    """
    get_owned(db, Catalog, catalog_id, user)
    statuses = [ReviewStatus.DRAFT]
    org = db.get(Organization, user.org_id)
    if org.approval_steps >= 2 and _is_admin(user):
        statuses.append(ReviewStatus.IN_REVIEW)
    versions = db.scalars(
        select(DescriptionVersion)
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(
            Product.catalog_id == catalog_id,
            DescriptionVersion.language == body.language,
            DescriptionVersion.is_current.is_(True),
            DescriptionVersion.status.in_(statuses),
            DescriptionVersion.seo_score >= body.min_seo_score,
        )
    ).all()
    chosen = [v for v in versions if not (body.skip_flagged and v.warnings)]
    return _approve_many(db, user, chosen, "Bulk approved")
