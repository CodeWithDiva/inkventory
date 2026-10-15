"""Audit trail. The request middleware stores the client IP / user agent in context variables so any
code path can record an entry without passing the request around."""

import logging
from contextvars import ContextVar

from sqlalchemy.orm import Session

from app.models import AuditLog, User

log = logging.getLogger(__name__)

client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)
user_agent: ContextVar[str | None] = ContextVar("user_agent", default=None)

ACTIONS = {
    "auth.login": "Signed in",
    "auth.login_failed": "Failed sign-in",
    "auth.logout": "Signed out",
    "auth.password_changed": "Changed password",
    "auth.password_reset": "Reset password by email",
    "auth.2fa_enabled": "Turned on two-step login",
    "auth.2fa_disabled": "Turned off two-step login",
    "auth.session_revoked": "Signed out a device",
    "user.created": "Added a team member",
    "user.updated": "Changed a team member",
    "user.deleted": "Deleted their account",
    "settings.updated": "Changed workspace settings",
    "api_key.created": "Created an API key",
    "api_key.revoked": "Revoked an API key",
    "webhook.created": "Added a webhook",
    "webhook.deleted": "Deleted a webhook",
    "integration.created": "Connected a store",
    "integration.deleted": "Removed a store connection",
    "catalog.imported": "Imported products",
    "catalog.deleted": "Deleted a catalog",
    "job.created": "Started a generation job",
    "job.cancelled": "Cancelled a generation job",
    "description.approved": "Approved descriptions",
    "description.rejected": "Rejected descriptions",
    "export.created": "Exported descriptions",
    "billing.plan_changed": "Plan changed",
    "data.exported": "Downloaded all workspace data",
    "org.deleted": "Deleted the workspace",
}


def actor_name(user: User | None) -> str:
    if user is None:
        return "system"
    return user.full_name if getattr(user, "api_key_id", None) else user.email


def record(
    db: Session,
    user: User | None,
    action: str,
    *,
    org_id: int | None = None,
    target_type: str | None = None,
    target_id: int | str | None = None,
    details: dict | None = None,
    commit: bool = True,
) -> None:
    """Add an audit entry. Never raises: auditing must not break the action itself."""
    try:
        db.add(AuditLog(
            org_id=org_id if org_id is not None else (user.org_id if user else None),
            user_id=user.id if user else None,
            actor=actor_name(user),
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details or {},
            ip=client_ip.get(),
        ))
        if commit:
            db.commit()
    except Exception:
        log.exception("Could not write audit entry %s", action)
        db.rollback()
