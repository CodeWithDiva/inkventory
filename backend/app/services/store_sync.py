"""Import products from a connected Shopify / WooCommerce store into a catalog (manual or scheduled)."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.crypto import decrypt_config
from app.db import SessionLocal
from app.models import Catalog, Integration, JobStatus, SyncRun
from app.services import webhooks
from app.services.csv_import import ProductRow
from app.services.exporters.platforms import PlatformError, get_client

log = logging.getLogger(__name__)
BATCH = 200


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes
    return value


def run_sync(sync_id: int) -> None:
    from app.routers.catalogs import upsert_products

    db = SessionLocal()
    run: SyncRun | None = None
    try:
        run = db.get(SyncRun, sync_id)
        if run is None or run.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return
        run.status = JobStatus.RUNNING
        db.commit()
        integration = db.get(Integration, run.integration_id)

        catalog = db.get(Catalog, run.catalog_id) if run.catalog_id else None
        if catalog is None:
            catalog = Catalog(org_id=run.org_id, name=f"{integration.name} ({integration.platform})",
                              source="api", created_by=run.created_by)
            db.add(catalog)
            db.flush()
            run.catalog_id = catalog.id
        integration.sync_catalog_id = catalog.id
        db.commit()

        client = get_client(integration.platform, decrypt_config(integration.config))
        batch: list[ProductRow] = []
        seen: set[str] = set()

        def flush() -> None:
            inserted, updated = upsert_products(db, catalog, batch)
            run.imported += inserted
            run.updated += updated
            db.commit()
            batch.clear()

        for item in client.list_products():
            sku, name = (item.get("sku") or "").strip()[:120], (item.get("name") or "").strip()[:500]
            if not sku or not name or sku in seen:
                run.errors = [*(run.errors or []), {"sku": sku, "error": "Missing SKU/name or duplicate SKU"}][-100:]
                continue
            seen.add(sku)
            batch.append(ProductRow(
                sku=sku, name=name, category=item.get("category"), brand=item.get("brand"), price=item.get("price"),
                keywords=item.get("keywords") or [], original_description=item.get("description") or None,
                attributes={k: v for k, v in (item.get("attributes") or {}).items() if v},
                images=item.get("images") or [],
            ))
            if len(batch) >= BATCH:
                flush()
        if batch:
            flush()

        run.status = JobStatus.COMPLETED
        integration.last_synced_at = now()
        db.commit()
        webhooks.safe_emit(db, run.org_id, "catalog.imported", {
            "catalog_id": catalog.id, "source": integration.platform, "imported": run.imported, "updated": run.updated})
    except Exception as exc:
        log.exception("Store sync %s failed", sync_id)
        db.rollback()
        run = db.get(SyncRun, sync_id)
        if run is not None:
            run.status = JobStatus.FAILED
            message = str(exc) if isinstance(exc, PlatformError) else f"Sync failed: {type(exc).__name__}"
            run.errors = [*(run.errors or []), {"error": message}]
    finally:
        if run is not None:
            run.finished_at = now()
            db.commit()
        db.close()


def start_sync(db, integration: Integration, *, trigger: str, user_id: int | None, catalog_id: int | None) -> SyncRun:
    from app.tasks import dispatch_sync

    run = SyncRun(org_id=integration.org_id, integration_id=integration.id, trigger=trigger,
                  catalog_id=catalog_id or integration.sync_catalog_id, created_by=user_id)
    db.add(run)
    db.commit()
    dispatch_sync(run.id)
    db.refresh(run)
    return run


def run_due_syncs() -> int:
    """Start scheduled syncs that are due. Called every few minutes by the scheduler."""
    started = 0
    with SessionLocal() as db:
        integrations = db.scalars(select(Integration).where(Integration.auto_sync_hours.is_not(None))).all()
        for integration in integrations:
            last = _aware(integration.last_synced_at)
            if last and now() - last < timedelta(hours=integration.auto_sync_hours):
                continue
            busy = db.scalar(select(SyncRun.id).where(
                SyncRun.integration_id == integration.id, SyncRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING])))
            if busy:
                continue
            start_sync(db, integration, trigger="schedule", user_id=None, catalog_id=None)
            started += 1
    return started
