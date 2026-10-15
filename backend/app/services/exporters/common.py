import html
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DescriptionVersion, Product, ReviewStatus


def approved_versions(db: Session, catalog_id: int, language: str) -> list[tuple[Product, DescriptionVersion]]:
    """Latest approved version per product for one language."""
    latest = (
        select(DescriptionVersion.product_id, func.max(DescriptionVersion.version_no).label("v"))
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(
            Product.catalog_id == catalog_id,
            DescriptionVersion.language == language,
            DescriptionVersion.status == ReviewStatus.APPROVED,
        )
        .group_by(DescriptionVersion.product_id)
        .subquery()
    )
    rows = db.execute(
        select(Product, DescriptionVersion)
        .join(DescriptionVersion, DescriptionVersion.product_id == Product.id)
        .join(latest, (latest.c.product_id == DescriptionVersion.product_id) & (latest.c.v == DescriptionVersion.version_no))
        .where(DescriptionVersion.language == language)
        .order_by(Product.id)
    ).all()
    return [(p, v) for p, v in rows]


def to_html(version: DescriptionVersion) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", version.long_description) if p.strip()]
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    if version.bullet_points:
        body += "<ul>" + "".join(f"<li>{html.escape(b)}</li>" for b in version.bullet_points) + "</ul>"
    return body


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "product"
