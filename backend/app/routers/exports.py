from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto import decrypt_config, encrypt_config, is_secret_field, mask
from app.db import get_db
from app.deps import can_admin, can_edit, get_current_user, get_owned
from app.models import Catalog, ExportJob, Integration, JobStatus, SyncRun, User
from app.schemas import ExportCreate, ExportOut, IntegrationIn, IntegrationOut, SyncRunOut, SyncSettings, SyncStart
from app.services import audit
from app.services.exporters import PLATFORM_FORMATS
from app.services.exporters.platforms import PlatformError, get_client
from app.services.prompts import LANGUAGES
from app.services.storage import read_file
from app.tasks import dispatch_export

router = APIRouter(tags=["export"])

MASK = "••••"


def _mask(config: dict) -> dict:
    """Secrets are stored encrypted and only ever returned masked."""
    return {k: mask(v) if is_secret_field(k) and v else v for k, v in config.items()}


def _out(integration: Integration) -> IntegrationOut:
    out = IntegrationOut.model_validate(integration)
    out.config = _mask(integration.config)
    return out


# ---------- Integrations (store connections) ----------
@router.get("/integrations", response_model=list[IntegrationOut])
def list_integrations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Integration).where(Integration.org_id == user.org_id).order_by(Integration.id))
    return [_out(i) for i in rows]


@router.post("/integrations", response_model=IntegrationOut, status_code=201)
def create_integration(body: IntegrationIn, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    try:
        get_client(body.platform, body.config)  # validates required config keys
    except PlatformError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    integration = Integration(
        org_id=user.org_id, platform=body.platform, name=body.name, config=encrypt_config(body.config)
    )
    db.add(integration)
    db.commit()
    audit.record(db, user, "integration.created", target_type="integration", target_id=integration.id,
                 details={"platform": body.platform, "name": body.name})
    return _out(integration)


@router.put("/integrations/{integration_id}", response_model=IntegrationOut)
def update_integration(integration_id: int, body: IntegrationIn, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    integration = get_owned(db, Integration, integration_id, user)
    # Masked values sent back unchanged keep the stored secret.
    config = {k: (integration.config.get(k, "") if str(v).startswith(MASK) else v) for k, v in body.config.items()}
    try:
        get_client(body.platform, decrypt_config(config))  # validates required keys and the address
    except PlatformError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    integration.platform, integration.name, integration.config = body.platform, body.name, encrypt_config(config)
    db.commit()
    return _out(integration)


@router.delete("/integrations/{integration_id}", status_code=204)
def delete_integration(integration_id: int, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    integration = get_owned(db, Integration, integration_id, user)
    details = {"platform": integration.platform, "name": integration.name}
    db.delete(integration)
    db.commit()
    audit.record(db, user, "integration.deleted", target_type="integration", target_id=integration_id, details=details)


@router.post("/integrations/{integration_id}/test")
def test_integration(integration_id: int, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    integration = get_owned(db, Integration, integration_id, user)
    try:
        detail = get_client(integration.platform, decrypt_config(integration.config)).test()
    except PlatformError as exc:
        return {"ok": False, "detail": str(exc)}
    except Exception as exc:  # network errors etc.
        return {"ok": False, "detail": f"Connection failed: {exc}"}
    return {"ok": True, "detail": detail}


# ---------- Product import / sync from the store ----------
@router.post("/integrations/{integration_id}/sync", response_model=SyncRunOut, status_code=201)
def start_store_sync(integration_id: int, body: SyncStart, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    """Import (or refresh) products from the connected store into a catalog, matched by SKU."""
    from app.services.store_sync import start_sync

    integration = get_owned(db, Integration, integration_id, user)
    if body.catalog_id:
        get_owned(db, Catalog, body.catalog_id, user)
    busy = db.scalar(select(SyncRun.id).where(SyncRun.integration_id == integration.id,
                                              SyncRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING])))
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "A sync is already running for this store")
    return start_sync(db, integration, trigger="manual", user_id=user.id, catalog_id=body.catalog_id)


@router.patch("/integrations/{integration_id}/sync-settings", response_model=IntegrationOut)
def update_sync_settings(integration_id: int, body: SyncSettings, user: User = Depends(can_admin),
                         db: Session = Depends(get_db)):
    integration = get_owned(db, Integration, integration_id, user)
    if body.sync_catalog_id is not None:
        get_owned(db, Catalog, body.sync_catalog_id, user)
        integration.sync_catalog_id = body.sync_catalog_id
    integration.auto_sync_hours = body.auto_sync_hours
    db.commit()
    return _out(integration)


@router.get("/integrations/{integration_id}/syncs", response_model=list[SyncRunOut])
def list_syncs(integration_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned(db, Integration, integration_id, user)
    return db.scalars(select(SyncRun).where(SyncRun.integration_id == integration_id)
                      .order_by(SyncRun.id.desc()).limit(20)).all()


# ---------- Exports ----------
@router.post("/exports", response_model=ExportOut, status_code=201)
def create_export(body: ExportCreate, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    get_owned(db, Catalog, body.catalog_id, user)
    if body.language not in LANGUAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported language")
    if body.format in PLATFORM_FORMATS:
        if not body.integration_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a store connection for direct export")
        integration = get_owned(db, Integration, body.integration_id, user)
        if integration.platform != body.format:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Integration platform does not match export format")
    export = ExportJob(org_id=user.org_id, created_by=user.id, **body.model_dump())
    db.add(export)
    db.commit()
    audit.record(db, user, "export.created", target_type="export", target_id=export.id,
                 details={"catalog_id": body.catalog_id, "format": body.format, "language": body.language})
    dispatch_export(export.id)
    db.refresh(export)
    return export


@router.get("/exports", response_model=list[ExportOut])
def list_exports(
    catalog_id: int | None = None,
    limit: int = Query(20, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(ExportJob).where(ExportJob.org_id == user.org_id)
    if catalog_id:
        query = query.where(ExportJob.catalog_id == catalog_id)
    return db.scalars(query.order_by(ExportJob.id.desc()).limit(limit)).all()


@router.get("/exports/{export_id}", response_model=ExportOut)
def get_export(export_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned(db, ExportJob, export_id, user)


@router.get("/exports/{export_id}/download")
def download_export(export_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    export = get_owned(db, ExportJob, export_id, user)
    if not export.file_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This export has no file")
    filename = export.file_key.rsplit("/", 1)[-1]
    return Response(
        read_file(export.file_key),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
