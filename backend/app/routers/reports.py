"""Reporting: activity over time, SEO trend, team productivity, cost per catalog, quality issues."""

import csv
import io
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, get_owned
from app.models import Catalog, Comment, DescriptionVersion, GenerationJob, Product, ReviewStatus, User

router = APIRouter(prefix="/reports", tags=["reports"])


def _day(value) -> str:
    return value if isinstance(value, str) else value.isoformat()


def _versions_query(org_id: int, catalog_id: int | None):
    query = select(DescriptionVersion).where(DescriptionVersion.org_id == org_id)
    if catalog_id:
        query = query.join(Product, Product.id == DescriptionVersion.product_id).where(Product.catalog_id == catalog_id)
    return query


def build_summary(db: Session, user: User, days: int, catalog_id: int | None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    base = _versions_query(user.org_id, catalog_id).subquery()
    V = base.c

    def per_day(date_col, *conditions, agg=None):
        rows = db.execute(
            select(func.date(date_col), agg if agg is not None else func.count())
            .where(date_col >= since, *conditions).group_by(func.date(date_col))
        ).all()
        return {_day(d): v for d, v in rows if d is not None}

    generated = per_day(V.created_at, V.source == "ai")
    edited = per_day(V.created_at, V.source == "manual")
    approved = per_day(V.reviewed_at, V.status == ReviewStatus.APPROVED)
    rejected = per_day(V.reviewed_at, V.status == ReviewStatus.REJECTED)
    seo = per_day(V.created_at, V.source == "ai", agg=func.avg(V.seo_score))
    start = since.date()
    series = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        series.append({
            "date": d, "generated": generated.get(d, 0), "edited": edited.get(d, 0),
            "approved": approved.get(d, 0), "rejected": rejected.get(d, 0),
            "avg_seo": round(float(seo[d]), 1) if seo.get(d) is not None else None,
        })

    # Team productivity in the period
    team = {u.id: {"user_id": u.id, "name": u.full_name, "email": u.email, "generated": 0, "edited": 0,
                   "approved": 0, "rejected": 0, "comments": 0}
            for u in db.scalars(select(User).where(User.org_id == user.org_id))}
    for uid, source, n in db.execute(select(V.created_by, V.source, func.count()).where(V.created_at >= since)
                                     .group_by(V.created_by, V.source)).all():
        if uid in team and source in ("ai", "manual"):
            team[uid]["generated" if source == "ai" else "edited"] += n
    for uid, st, n in db.execute(select(V.reviewed_by, V.status, func.count()).where(V.reviewed_at >= since)
                                 .group_by(V.reviewed_by, V.status)).all():
        if uid in team and st in (ReviewStatus.APPROVED, ReviewStatus.REJECTED, ReviewStatus.IN_REVIEW):
            team[uid]["rejected" if st == ReviewStatus.REJECTED else "approved"] += n
    comment_q = select(Comment.user_id, func.count()).where(Comment.org_id == user.org_id, Comment.created_at >= since)
    if catalog_id:
        comment_q = comment_q.join(Product, Product.id == Comment.product_id).where(Product.catalog_id == catalog_id)
    for uid, n in db.execute(comment_q.group_by(Comment.user_id)).all():
        if uid in team:
            team[uid]["comments"] += n
    team_rows = sorted((t for t in team.values() if any(t[k] for k in ("generated", "edited", "approved", "rejected", "comments"))),
                       key=lambda t: -(t["approved"] + t["edited"] + t["generated"]))

    # Cost per catalog
    cost_q = (select(Catalog.id, Catalog.name, func.coalesce(func.sum(GenerationJob.cost_usd), 0),
                     func.coalesce(func.sum(GenerationJob.completed), 0))
              .join(GenerationJob, GenerationJob.catalog_id == Catalog.id)
              .where(Catalog.org_id == user.org_id, GenerationJob.created_at >= since)
              .group_by(Catalog.id, Catalog.name))
    if catalog_id:
        cost_q = cost_q.where(Catalog.id == catalog_id)
    costs = [{"catalog_id": cid, "catalog": name, "cost_usd": round(float(cost), 4), "generated": int(n)}
             for cid, name, cost, n in db.execute(cost_q).all()]

    # Current descriptions: status per language and quality warnings
    languages: dict[str, dict[str, int]] = {}
    warnings: Counter = Counter()
    seo_buckets = Counter()
    for lang, st, warns, score in db.execute(select(V.language, V.status, V.warnings, V.seo_score).where(V.is_current.is_(True))):
        languages.setdefault(lang, {"draft": 0, "in_review": 0, "approved": 0, "rejected": 0})[st] += 1
        for w in warns or []:
            warnings[w.get("type", "other")] += 1
        seo_buckets["80-100" if score >= 80 else "60-79" if score >= 60 else "0-59"] += 1

    totals = {k: sum(s[k] for s in series) for k in ("generated", "edited", "approved", "rejected")}
    scored = [s["avg_seo"] for s in series if s["avg_seo"] is not None]
    totals["avg_seo"] = round(sum(scored) / len(scored), 1) if scored else None
    totals["cost_usd"] = round(sum(c["cost_usd"] for c in costs), 4)
    return {
        "days": days, "since": start.isoformat(), "catalog_id": catalog_id, "totals": totals, "series": series,
        "team": team_rows, "cost_by_catalog": sorted(costs, key=lambda c: -c["cost_usd"]),
        "languages": languages, "warnings": dict(warnings.most_common()),
        "seo_distribution": {k: seo_buckets.get(k, 0) for k in ("0-59", "60-79", "80-100")},
    }


@router.get("/summary")
def summary(
    days: int = Query(30, ge=1, le=365),
    catalog_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if catalog_id:
        get_owned(db, Catalog, catalog_id, user)
    return build_summary(db, user, days, catalog_id)


@router.get("/daily.csv")
def daily_csv(
    days: int = Query(30, ge=1, le=365),
    catalog_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if catalog_id:
        get_owned(db, Catalog, catalog_id, user)
    data = build_summary(db, user, days, catalog_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "generated", "edited", "approved", "rejected", "avg_seo"])
    for row in data["series"]:
        writer.writerow([row["date"], row["generated"], row["edited"], row["approved"], row["rejected"],
                         "" if row["avg_seo"] is None else row["avg_seo"]])
    return Response("﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="report-{date.today().isoformat()}.csv"'})
