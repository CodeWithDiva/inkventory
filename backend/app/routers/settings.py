"""Organization settings: name, notifications and the organization's own Claude API key."""

import csv
import io
import json
import zipfile

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.crypto import encrypt, mask
from app.db import get_db
from app.deps import can_admin, get_current_user, require_session_roles
from app.models import (
    ApiKey,
    AuditLog,
    Catalog,
    DescriptionVersion,
    ExportJob,
    GenerationJob,
    GlossaryTerm,
    Integration,
    Organization,
    Product,
    Role,
    StylePreset,
    SyncRun,
    User,
    UserSession,
    Webhook,
    WebhookDelivery,
)
from app.schemas import AuditLogOut, DeleteOrgIn, OrgSettingsIn, OrgSettingsOut
from app.security import verify_password
from app.services import audit

router = APIRouter(prefix="/settings", tags=["settings"])


def _out(org: Organization) -> OrgSettingsOut:
    return OrgSettingsOut(
        name=org.name,
        has_own_anthropic_key=bool(org.anthropic_api_key),
        anthropic_key_hint=mask(org.anthropic_api_key) if org.anthropic_api_key else "",
        notify_on_job_complete=org.notify_on_job_complete,
        require_2fa=org.require_2fa,
        approval_steps=org.approval_steps,
    )


@router.get("", response_model=OrgSettingsOut)
def get_settings_(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(db.get(Organization, user.org_id))


@router.patch("", response_model=OrgSettingsOut)
def update_settings(body: OrgSettingsIn, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    org = db.get(Organization, user.org_id)
    if body.name is not None:
        org.name = body.name.strip()
    if body.notify_on_job_complete is not None:
        org.notify_on_job_complete = body.notify_on_job_complete
    if body.require_2fa is not None:
        if body.require_2fa and not user.totp_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Turn on two-step login for your own account first")
        org.require_2fa = body.require_2fa
    if body.approval_steps is not None:
        org.approval_steps = body.approval_steps
    if body.anthropic_api_key is not None:
        key = body.anthropic_api_key.strip()
        if key and not key.startswith("sk-ant-"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That does not look like a Claude API key (sk-ant-…)")
        org.anthropic_api_key = encrypt(key) if key else None
    db.commit()
    changed = body.model_dump(exclude_none=True)
    if "anthropic_api_key" in changed:
        changed["anthropic_api_key"] = "set" if changed["anthropic_api_key"] else "removed"
    audit.record(db, user, "settings.updated", details=changed)
    return _out(org)


@router.post("/test-anthropic-key")
def test_anthropic_key(user: User = Depends(can_admin), db: Session = Depends(get_db)):
    """Checks the saved key with a free call (listing models); no tokens are spent."""
    from app.crypto import decrypt

    org = db.get(Organization, user.org_id)
    if not org.anthropic_api_key:
        return {"ok": False, "detail": "No key saved"}
    try:
        anthropic.Anthropic(api_key=decrypt(org.anthropic_api_key), max_retries=0).models.list(limit=1)
    except anthropic.AuthenticationError:
        return {"ok": False, "detail": "The key was rejected (invalid or revoked)"}
    except anthropic.PermissionDeniedError:
        return {"ok": False, "detail": "The key does not have permission to use the API"}
    except anthropic.APIConnectionError:
        return {"ok": False, "detail": "Could not reach the Claude API"}
    except anthropic.APIStatusError as exc:
        return {"ok": False, "detail": f"Claude API error ({exc.status_code})"}
    return {"ok": True, "detail": "Key works. Note: generation also needs credits on that Anthropic account."}


# ---------- Audit log ----------
@router.get("/audit-log", response_model=list[AuditLogOut])
def audit_log(
    action: str | None = None,
    user_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    user: User = Depends(can_admin),
    db: Session = Depends(get_db),
):
    query = select(AuditLog).where(AuditLog.org_id == user.org_id)
    if action:
        query = query.where(AuditLog.action.like(f"{action}%"))
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    return db.scalars(query.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()


@router.get("/audit-log/actions")
def audit_actions(user: User = Depends(can_admin)):
    return audit.ACTIONS


@router.get("/audit-log/export")
def audit_export(user: User = Depends(can_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.org_id == user.org_id).order_by(AuditLog.id.desc()).limit(50_000))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["time", "actor", "action", "target_type", "target_id", "details", "ip"])
    for r in rows:
        writer.writerow([r.created_at.isoformat(), r.actor, r.action, r.target_type or "", r.target_id or "",
                         json.dumps(r.details, ensure_ascii=False), r.ip or ""])
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})


# ---------- Data export & deletion (GDPR) ----------
def _rows(db: Session, model, org_id: int, exclude: tuple[str, ...] = ()) -> list[dict]:
    out = []
    for row in db.scalars(select(model).where(model.org_id == org_id)):
        data = {c.name: getattr(row, c.name) for c in model.__table__.columns if c.name not in exclude}
        out.append(data)
    return out


@router.get("/export-data")
def export_data(user: User = Depends(require_session_roles(Role.OWNER)), db: Session = Depends(get_db)):
    """Everything stored for this workspace as a ZIP of JSON files (secrets and password hashes excluded)."""
    org = db.get(Organization, user.org_id)
    secret_cols = ("password_hash", "totp_secret", "recovery_codes", "anthropic_api_key", "key_hash", "secret", "config")
    files = {
        "organization.json": [{c.name: getattr(org, c.name) for c in Organization.__table__.columns if c.name not in secret_cols}],
        "users.json": _rows(db, User, org.id, secret_cols),
        "catalogs.json": _rows(db, Catalog, org.id),
        "products.json": _rows(db, Product, org.id),
        "descriptions.json": _rows(db, DescriptionVersion, org.id),
        "style_presets.json": _rows(db, StylePreset, org.id),
        "glossary.json": _rows(db, GlossaryTerm, org.id),
        "generation_jobs.json": _rows(db, GenerationJob, org.id),
        "exports.json": _rows(db, ExportJob, org.id),
        "store_connections.json": _rows(db, Integration, org.id, secret_cols),
        "api_keys.json": _rows(db, ApiKey, org.id, secret_cols),
        "webhooks.json": _rows(db, Webhook, org.id, secret_cols),
        "audit_log.json": _rows(db, AuditLog, org.id),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, rows in files.items():
            zf.writestr(name, json.dumps(rows, default=str, ensure_ascii=False, indent=2))
        zf.writestr("README.txt", "Export of all data stored for this workspace. Secrets are excluded.\n")
    audit.record(db, user, "data.exported")
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="workspace-export.zip"'})


@router.delete("/organization", status_code=204)
def delete_organization(body: DeleteOrgIn, user: User = Depends(require_session_roles(Role.OWNER)),
                        db: Session = Depends(get_db)):
    """Permanently delete the workspace and everything in it. Owner only, needs name + password."""
    org = db.get(Organization, user.org_id)
    if body.organization_name.strip() != org.name.strip() or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workspace name or password is not correct")
    org_id = org.id
    # Children first (works on SQLite, where foreign-key cascades are not enforced by default).
    product_ids = select(Product.id).where(Product.org_id == org_id)
    hook_ids = select(Webhook.id).where(Webhook.org_id == org_id)
    user_ids = select(User.id).where(User.org_id == org_id)
    for stmt in (
        delete(WebhookDelivery).where(WebhookDelivery.webhook_id.in_(hook_ids)),
        delete(DescriptionVersion).where(DescriptionVersion.product_id.in_(product_ids)),
        *(delete(m).where(m.org_id == org_id) for m in (
            SyncRun, ExportJob, GenerationJob, Product, Integration, Catalog, GlossaryTerm, StylePreset, ApiKey,
            Webhook, AuditLog, UserSession)),
        delete(User).where(User.id.in_(user_ids)),
        delete(Organization).where(Organization.id == org_id),
    ):
        db.execute(stmt)
    db.commit()