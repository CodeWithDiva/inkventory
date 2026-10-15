import json
from collections.abc import Iterable

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import can_edit, get_current_user, get_owned
from app.models import Catalog, DescriptionVersion, GenerationJob, Organization, Product, User
from app.schemas import (
    CatalogApiCreate,
    CatalogCard,
    CatalogDetail,
    CatalogOut,
    CatalogPreview,
    CatalogStats,
    ImportResult,
    Page,
    ProductOut,
    ProductRow,
    ProductsBulkIn,
    ProductUpdate,
    UrlImportIn,
    VersionSummary,
)
from app.services import audit, csv_import, net, webhooks
from app.services.images import ALLOWED_TYPES, STORAGE_PREFIX
from app.services.storage import read_file, save_file

router = APIRouter(tags=["catalogs"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith((".csv", ".txt", ".tsv")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please upload a .csv file")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is larger than 25 MB")
    return data


def upsert_products(db: Session, catalog: Catalog, rows: Iterable) -> tuple[int, int]:
    """Insert new SKUs and update existing ones. Returns (inserted, updated)."""
    rows = list(rows)
    existing = {
        p.sku: p
        for p in db.scalars(
            select(Product).where(Product.catalog_id == catalog.id, Product.sku.in_([r.sku for r in rows]))
        )
    }
    inserted = updated = 0
    for r in rows:
        fields = dict(
            name=r.name, category=r.category, brand=r.brand, price=r.price,
            attributes=dict(r.attributes), keywords=list(r.keywords),
            original_description=r.original_description,
        )
        product = existing.get(r.sku)
        urls = list(getattr(r, "images", []) or [])
        if product:
            # Re-imports refresh image URLs but keep photos uploaded in the app.
            uploaded = [i for i in (product.images or []) if i.startswith(STORAGE_PREFIX)]
            fields["images"] = uploaded + [u for u in urls if u not in uploaded]
            for key, value in fields.items():
                setattr(product, key, value)
            updated += 1
        else:
            db.add(Product(org_id=catalog.org_id, catalog_id=catalog.id, sku=r.sku, images=urls, **fields))
            inserted += 1
    db.flush()
    catalog.product_count = db.scalar(select(func.count(Product.id)).where(Product.catalog_id == catalog.id))
    return inserted, updated


def _preview(parsed: csv_import.ParsedCatalog) -> CatalogPreview:
    shopify = csv_import.is_shopify_export(parsed.headers)
    return CatalogPreview(
        headers=parsed.headers,
        sample_rows=parsed.rows[:5],
        row_count=len(parsed.rows),
        suggested_mapping=csv_import.suggest_mapping(parsed.headers),
        product_count=csv_import.count_products(parsed),
        detected_format="shopify" if shopify else None,
    )


@router.post("/catalogs/preview", response_model=CatalogPreview)
async def preview_catalog(file: UploadFile = File(...), user: User = Depends(can_edit)):
    """Step 1 of upload: show headers, sample rows and the suggested column mapping."""
    try:
        parsed = csv_import.parse_csv(await _read_upload(file), get_settings().max_upload_rows)
    except csv_import.CSVImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return _preview(parsed)


@router.post("/catalogs", response_model=ImportResult, status_code=201)
async def upload_catalog(
    file: UploadFile = File(...),
    name: str = Form(...),
    mapping: str = Form("{}", description='JSON, e.g. {"sku": "Product ID", "name": "Title"}'),
    catalog_id: int | None = Form(None, description="Add to / update an existing catalog"),
    user: User = Depends(can_edit),
    db: Session = Depends(get_db),
):
    """Step 2 of upload: import the file with the confirmed column mapping."""
    data = await _read_upload(file)
    try:
        parsed = csv_import.parse_csv(data, get_settings().max_upload_rows)
        column_mapping = json.loads(mapping) or csv_import.suggest_mapping(parsed.headers)
        if not isinstance(column_mapping, dict):
            raise csv_import.CSVImportError("mapping must be a JSON object")
        rows, errors = csv_import.build_products(parsed, column_mapping)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "mapping is not valid JSON")
    except csv_import.CSVImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"message": "No valid rows found", "errors": errors[:50]})

    if catalog_id:
        catalog = get_owned(db, Catalog, catalog_id, user)
    else:
        catalog = Catalog(org_id=user.org_id, name=name.strip()[:200] or "Untitled catalog", created_by=user.id)
        db.add(catalog)
        db.flush()
    catalog.source = "csv"
    catalog.source_filename = file.filename
    catalog.column_mapping = column_mapping
    catalog.file_key = save_file(f"org_{user.org_id}/catalogs/{catalog.id}/{file.filename}", data)
    inserted, updated = upsert_products(db, catalog, rows)
    db.commit()
    _imported_event(db, catalog, "csv", inserted, updated, user)
    return ImportResult(catalog=catalog, imported=inserted, updated=updated, errors=errors[:200])


def _imported_event(db: Session, catalog: Catalog, source: str, inserted: int, updated: int,
                    user: User | None = None) -> None:
    if user is not None:
        audit.record(db, user, "catalog.imported", target_type="catalog", target_id=catalog.id,
                     details={"source": source, "imported": inserted, "updated": updated})
    webhooks.safe_emit(db, catalog.org_id, "catalog.imported", {
        "catalog_id": catalog.id, "source": source, "imported": inserted, "updated": updated})


def _fetch_url(url: str) -> bytes:
    try:
        return net.fetch(net.google_sheets_csv_url(url.strip()))
    except (net.UnsafeURLError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not download that URL")


@router.post("/catalogs/preview-url", response_model=CatalogPreview)
def preview_url(body: UrlImportIn, user: User = Depends(can_edit)):
    """Like /catalogs/preview, for a Google Sheets link (shared as "anyone with the link") or a CSV URL."""
    try:
        parsed = csv_import.parse_csv(_fetch_url(body.url), get_settings().max_upload_rows)
    except csv_import.CSVImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return _preview(parsed)


@router.post("/catalogs/import-url", response_model=ImportResult, status_code=201)
def import_url(body: UrlImportIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    data = _fetch_url(body.url)
    try:
        parsed = csv_import.parse_csv(data, get_settings().max_upload_rows)
        mapping = body.mapping or csv_import.suggest_mapping(parsed.headers)
        rows, errors = csv_import.build_products(parsed, mapping)
    except csv_import.CSVImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"message": "No valid rows found", "errors": errors[:50]})
    if body.catalog_id:
        catalog = get_owned(db, Catalog, body.catalog_id, user)
    else:
        catalog = Catalog(org_id=user.org_id, name=body.name.strip() or "Imported catalog", created_by=user.id)
        db.add(catalog)
        db.flush()
    catalog.source, catalog.source_filename, catalog.column_mapping = "url", body.url[:255], mapping
    inserted, updated = upsert_products(db, catalog, rows)
    db.commit()
    _imported_event(db, catalog, "url", inserted, updated, user)
    return ImportResult(catalog=catalog, imported=inserted, updated=updated, errors=errors[:200])


@router.post("/catalogs/api", response_model=ImportResult, status_code=201)
def create_catalog_api(body: CatalogApiCreate, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    """Create a catalog via API (for integrations that push products programmatically)."""
    catalog = Catalog(org_id=user.org_id, name=body.name, source="api", created_by=user.id)
    db.add(catalog)
    db.flush()
    inserted, updated = upsert_products(db, catalog, body.products)
    db.commit()
    _imported_event(db, catalog, "api", inserted, updated, user)
    return ImportResult(catalog=catalog, imported=inserted, updated=updated, errors=[])


@router.post("/catalogs/{catalog_id}/products", response_model=ImportResult)
def add_products(catalog_id: int, body: ProductsBulkIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    """Bulk add or update products (matched by SKU) via API."""
    catalog = get_owned(db, Catalog, catalog_id, user)
    inserted, updated = upsert_products(db, catalog, body.products)
    db.commit()
    return ImportResult(catalog=catalog, imported=inserted, updated=updated, errors=[])


@router.get("/catalogs", response_model=list[CatalogCard])
def list_catalogs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    catalogs = db.scalars(select(Catalog).where(Catalog.org_id == user.org_id).order_by(Catalog.id.desc())).all()
    ids = [c.id for c in catalogs]
    if not ids:
        return []
    counts: dict[int, dict[str, int]] = {}
    for catalog_id, st, count in db.execute(
        select(Product.catalog_id, DescriptionVersion.status, func.count())
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(Product.catalog_id.in_(ids), DescriptionVersion.is_current.is_(True))
        .group_by(Product.catalog_id, DescriptionVersion.status)
    ).all():
        counts.setdefault(catalog_id, {})[st] = count
    last_job_ids = db.execute(
        select(GenerationJob.catalog_id, func.max(GenerationJob.id))
        .where(GenerationJob.catalog_id.in_(ids)).group_by(GenerationJob.catalog_id)
    ).all()
    job_status = {
        j.catalog_id: j.status
        for j in db.scalars(select(GenerationJob).where(GenerationJob.id.in_([jid for _, jid in last_job_ids])))
    }
    cards = []
    for c in catalogs:
        with_photos = db.scalars(
            select(Product).where(Product.catalog_id == c.id, func.json_array_length(Product.images) > 0)
            .order_by(Product.id).limit(3)
        ).all()
        thumbs = [
            {"product_id": p.id, "index": 0, "url": p.images[0] if p.images[0].startswith("http") else None}
            for p in with_photos
        ]
        by_status = counts.get(c.id, {})
        cards.append(CatalogCard.model_validate({
            **CatalogOut.model_validate(c).model_dump(),
            "thumbnails": thumbs,
            "generated": sum(by_status.values()),
            "approved": by_status.get("approved", 0),
            "pending_review": by_status.get("draft", 0) + by_status.get("in_review", 0),
            "last_job_status": job_status.get(c.id),
        }))
    return cards


def catalog_stats(db: Session, catalog_id: int) -> CatalogStats:
    rows = db.execute(
        select(DescriptionVersion.language, DescriptionVersion.status, func.count())
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(Product.catalog_id == catalog_id, DescriptionVersion.is_current.is_(True))
        .group_by(DescriptionVersion.language, DescriptionVersion.status)
    ).all()
    by_language: dict[str, dict[str, int]] = {}
    for lang, st, count in rows:
        by_language.setdefault(lang, {"draft": 0, "in_review": 0, "approved": 0, "rejected": 0})[st] = count
    avg = db.scalar(
        select(func.avg(DescriptionVersion.seo_score))
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(Product.catalog_id == catalog_id, DescriptionVersion.is_current.is_(True))
    )
    total = db.scalar(select(func.count(Product.id)).where(Product.catalog_id == catalog_id)) or 0
    org = db.get(Organization, db.get(Catalog, catalog_id).org_id)
    return CatalogStats(total_products=total, by_language=by_language, approval_steps=org.approval_steps,
                        avg_seo_score=round(float(avg), 1) if avg is not None else None)


@router.get("/catalogs/{catalog_id}", response_model=CatalogDetail)
def get_catalog(catalog_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    catalog = get_owned(db, Catalog, catalog_id, user)
    return CatalogDetail.model_validate(
        {**CatalogOut.model_validate(catalog).model_dump(), "column_mapping": catalog.column_mapping,
         "stats": catalog_stats(db, catalog.id)}
    )


@router.delete("/catalogs/{catalog_id}", status_code=204)
def delete_catalog(catalog_id: int, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    catalog = get_owned(db, Catalog, catalog_id, user)
    name = catalog.name
    db.delete(catalog)
    db.commit()
    audit.record(db, user, "catalog.deleted", target_type="catalog", target_id=catalog_id, details={"name": name})


STATUS_PATTERN = "^(missing|draft|in_review|approved|rejected|warnings|pending)$"


@router.get("/catalogs/{catalog_id}/products", response_model=Page)
def list_products(
    catalog_id: int,
    search: str | None = None,
    language: str = "en",
    status_filter: str | None = Query(None, alias="status", pattern=STATUS_PATTERN),
    assignee: str | None = Query(None, description="me, none, or a user id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned(db, Catalog, catalog_id, user)
    return product_page(db, user, select(Product).where(Product.catalog_id == catalog_id),
                        search, language, status_filter, assignee, page, page_size)


@router.get("/products", response_model=Page)
def list_all_products(
    catalog_id: int | None = None,
    search: str | None = None,
    language: str = "en",
    status_filter: str | None = Query(None, alias="status", pattern=STATUS_PATTERN),
    assignee: str | None = Query(None, description="me, none, or a user id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Products across every catalog of the workspace (Products and Review & Approve pages)."""
    query = select(Product).where(Product.org_id == user.org_id)
    if catalog_id:
        query = query.where(Product.catalog_id == catalog_id)
    return product_page(db, user, query, search, language, status_filter, assignee, page, page_size, with_catalog=True)


def product_page(db: Session, user: User, query, search, language, status_filter, assignee, page, page_size,
                 with_catalog: bool = False) -> Page:
    if assignee == "me":
        query = query.where(Product.assignee_id == user.id)
    elif assignee == "none":
        query = query.where(Product.assignee_id.is_(None))
    elif assignee and assignee.isdigit():
        query = query.where(Product.assignee_id == int(assignee))
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(Product.sku.ilike(like), Product.name.ilike(like)))
    if status_filter:
        current = select(DescriptionVersion.id).where(
            DescriptionVersion.product_id == Product.id,
            DescriptionVersion.language == language,
            DescriptionVersion.is_current.is_(True),
        )
        if status_filter == "missing":
            query = query.where(~exists(current))
        elif status_filter == "pending":
            query = query.where(exists(current.where(DescriptionVersion.status.in_(["draft", "in_review"]))))
        elif status_filter == "warnings":
            # json_array_length exists in both SQLite and PostgreSQL (for JSON columns).
            query = query.where(exists(current.where(func.json_array_length(DescriptionVersion.warnings) > 0)))
        else:
            query = query.where(exists(current.where(DescriptionVersion.status == status_filter)))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    products = db.scalars(query.order_by(Product.id).offset((page - 1) * page_size).limit(page_size)).all()

    versions = db.scalars(
        select(DescriptionVersion).where(
            DescriptionVersion.product_id.in_([p.id for p in products]),
            DescriptionVersion.is_current.is_(True),
        )
    ).all()
    current_map: dict[int, dict[str, VersionSummary]] = {}
    for v in versions:
        current_map.setdefault(v.product_id, {})[v.language] = VersionSummary.model_validate(v)

    names: dict[int, str] = {}
    if with_catalog and products:
        names = dict(db.execute(
            select(Catalog.id, Catalog.name).where(Catalog.id.in_({p.catalog_id for p in products}))
        ).all())
    items = [
        ProductRow.model_validate({**ProductOut.model_validate(p).model_dump(), "current": current_map.get(p.id, {}),
                                   "catalog_name": names.get(p.catalog_id)})
        for p in products
    ]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned(db, Product, product_id, user)


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: int, body: ProductUpdate, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    product = get_owned(db, Product, product_id, user)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(product, field, value)
    db.commit()
    return product


# ---------- Product photos ----------
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 10


@router.post("/products/{product_id}/images", response_model=ProductOut)
async def upload_image(
    product_id: int, file: UploadFile = File(...), user: User = Depends(can_edit), db: Session = Depends(get_db)
):
    """Attach a photo; vision-capable AI engines use it for visible details (color, shape, style)."""
    product = get_owned(db, Product, product_id, user)
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a JPG, PNG, WEBP or GIF image")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image is larger than 5 MB")
    if len(product.images or []) >= MAX_IMAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"A product can have at most {MAX_IMAGES} images")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}[file.content_type]
    key = save_file(f"org_{user.org_id}/images/{product.id}/{uuid.uuid4().hex}.{ext}", data)
    product.images = [*(product.images or []), STORAGE_PREFIX + key]
    db.commit()
    return product


@router.post("/products/{product_id}/image-urls", response_model=ProductOut)
def add_image_url(product_id: int, body: dict, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    product = get_owned(db, Product, product_id, user)
    url = str(body.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter an http(s) image URL")
    if len(product.images or []) >= MAX_IMAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"A product can have at most {MAX_IMAGES} images")
    product.images = [*(product.images or []), url[:1000]]
    db.commit()
    return product


@router.delete("/products/{product_id}/images/{index}", response_model=ProductOut)
def delete_image(product_id: int, index: int, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    product = get_owned(db, Product, product_id, user)
    images = list(product.images or [])
    if not 0 <= index < len(images):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    images.pop(index)
    product.images = images
    db.commit()
    return product


@router.post("/products/{product_id}/analyze-photos", response_model=ProductOut)
def analyze_photos(product_id: int, body: dict | None = None, user: User = Depends(can_edit),
                   db: Session = Depends(get_db)):
    """Have a vision model study the product's photos now (also happens automatically before writing)."""
    from app.services.generation import ensure_photo_analysis
    from app.services.llm import LLMError

    product = get_owned(db, Product, product_id, user)
    if not product.images:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add a product photo first")
    org = db.get(Organization, user.org_id)
    try:
        analysis = ensure_photo_analysis(db, product, (body or {}).get("engine"), org, force=True)
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if analysis is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "No AI engine that can read photos is available (Gemini, Claude or a local vision model)")
    db.refresh(product)
    return product


@router.get("/products/{product_id}/images/{index}")
def get_image(product_id: int, index: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Serves uploaded photos (external URLs are loaded by the browser directly)."""
    product = get_owned(db, Product, product_id, user)
    images = product.images or []
    if not 0 <= index < len(images) or not images[index].startswith(STORAGE_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    key = images[index][len(STORAGE_PREFIX):]
    media = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(
        key.rsplit(".", 1)[-1], "application/octet-stream")
    return Response(read_file(key), media_type=media, headers={"Cache-Control": "private, max-age=3600"})