from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Catalog, DescriptionVersion, GenerationJob, Organization, Product, User
from app.schemas import JobOut, OptionsOut
from app.services.generation import monthly_usage
from app.services.llm import available_engines, default_engine, get_provider

router = APIRouter(tags=["dashboard"])


@router.get("/options", response_model=OptionsOut)
def options(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Languages, tones, lengths and the AI engines this organization can use."""
    engines = available_engines(db.get(Organization, user.org_id))
    default = default_engine()
    try:
        model = get_provider(default).model
    except Exception:  # e.g. Claude selected as default but no API key configured yet
        model = next((e["label"] for e in engines if e["id"] == default), default)
    return OptionsOut.build(get_settings().llm_provider, model, default, engines)


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.get(Organization, user.org_id)
    org_filter = DescriptionVersion.org_id == user.org_id
    current = DescriptionVersion.is_current.is_(True)

    status_counts = dict(
        db.execute(
            select(DescriptionVersion.status, func.count()).where(org_filter, current).group_by(DescriptionVersion.status)
        ).all()
    )
    avg_seo = db.scalar(select(func.avg(DescriptionVersion.seo_score)).where(org_filter, current))
    flagged = db.scalar(
        select(func.count()).where(org_filter, current, func.json_array_length(DescriptionVersion.warnings) > 0)
    )
    totals = db.execute(
        select(func.coalesce(func.sum(GenerationJob.cost_usd), 0), func.coalesce(func.sum(GenerationJob.completed), 0))
        .where(GenerationJob.org_id == user.org_id)
    ).one()
    recent_jobs = db.scalars(
        select(GenerationJob).where(GenerationJob.org_id == user.org_id).order_by(GenerationJob.id.desc()).limit(5)
    ).all()
    return {
        "catalogs": db.scalar(select(func.count(Catalog.id)).where(Catalog.org_id == user.org_id)),
        "products": db.scalar(select(func.count(Product.id)).where(Product.org_id == user.org_id)),
        "descriptions": {
            "draft": status_counts.get("draft", 0),
            "in_review": status_counts.get("in_review", 0),
            "approved": status_counts.get("approved", 0),
            "rejected": status_counts.get("rejected", 0),
            "flagged": flagged or 0,
        },
        "avg_seo_score": round(float(avg_seo), 1) if avg_seo is not None else None,
        "usage": {"this_month": monthly_usage(db, user.org_id), "limit": org.monthly_generation_limit},
        "total_cost_usd": round(float(totals[0]), 4),
        "total_generated": int(totals[1]),
        "recent_jobs": [JobOut.model_validate(j).model_dump(mode="json") for j in recent_jobs],
        "quality": quality_overview(db, user.org_id),
        "attention": needs_attention(db, user.org_id),
        "trend": daily_trend(db, user.org_id),
    }


# Accuracy problems first: they are the ones a reviewer must not miss.
FLAG_ORDER = ["unverified_facts", "wrong_language", "photo_mismatch", "duplicate_content", "channel_rules", "glossary", "ai_style",
              "glossary_fixed", "note"]


def quality_overview(db: Session, org_id: int) -> dict:
    rows = db.execute(
        select(DescriptionVersion.seo_score, DescriptionVersion.warnings)
        .where(DescriptionVersion.org_id == org_id, DescriptionVersion.is_current.is_(True))
    ).all()
    buckets = {"low": 0, "medium": 0, "high": 0}
    flags: dict[str, int] = {}
    clean = 0
    for score, warnings in rows:
        buckets["high" if score >= 80 else "medium" if score >= 60 else "low"] += 1
        types = {w.get("type", "note") for w in warnings or [] if w.get("type") != "glossary_fixed"}
        clean += not types
        for kind in types:
            flags[kind] = flags.get(kind, 0) + 1
    return {
        "total": len(rows),
        "seo_buckets": buckets,
        "flags": dict(sorted(flags.items(), key=lambda kv: FLAG_ORDER.index(kv[0]) if kv[0] in FLAG_ORDER else 99)),
        "clean_share": round(clean / len(rows) * 100) if rows else None,
    }


def needs_attention(db: Session, org_id: int, limit: int = 6) -> list[dict]:
    """Drafts waiting for review that have quality flags or a weak SEO score, most serious first."""
    rows = db.execute(
        select(DescriptionVersion, Product.name, Product.sku, Product.catalog_id)
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(
            DescriptionVersion.org_id == org_id,
            DescriptionVersion.is_current.is_(True),
            DescriptionVersion.status.in_(["draft", "in_review"]),
        )
        .order_by(DescriptionVersion.seo_score)
        .limit(500)
    ).all()

    def severity(version: DescriptionVersion) -> tuple:
        types = [w.get("type") for w in version.warnings or [] if w.get("type") != "glossary_fixed"]
        rank = min((FLAG_ORDER.index(t) for t in types if t in FLAG_ORDER), default=99)
        return (rank, version.seo_score)

    items = []
    for version, name, sku, catalog_id in sorted(rows, key=lambda r: severity(r[0])):
        types = [w.get("type") for w in version.warnings or [] if w.get("type") != "glossary_fixed"]
        if not types and version.seo_score >= 70:
            continue
        items.append({
            "product_id": version.product_id, "catalog_id": catalog_id, "name": name, "sku": sku,
            "language": version.language, "seo_score": version.seo_score, "flags": sorted(set(types)),
            "message": next((w["message"] for w in version.warnings or [] if w.get("type") in types), None),
        })
        if len(items) >= limit:
            break
    return items


def daily_trend(db: Session, org_id: int, days: int = 14) -> list[dict]:
    start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    since = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    series = {start + timedelta(days=i): {"generated": 0, "approved": 0} for i in range(days)}

    def day(value: datetime) -> date:
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()

    for (created,) in db.execute(select(DescriptionVersion.created_at).where(
            DescriptionVersion.org_id == org_id, DescriptionVersion.source == "ai",
            DescriptionVersion.created_at >= since)):
        if day(created) in series:
            series[day(created)]["generated"] += 1
    for (reviewed,) in db.execute(select(DescriptionVersion.reviewed_at).where(
            DescriptionVersion.org_id == org_id, DescriptionVersion.status == "approved",
            DescriptionVersion.reviewed_at >= since)):
        if reviewed and day(reviewed) in series:
            series[day(reviewed)]["approved"] += 1
    return [{"date": d.isoformat(), **v} for d, v in series.items()]
