"""API keys and outgoing webhooks for connecting other systems."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto import encrypt
from app.db import get_db
from app.deps import can_admin, get_owned
from app.models import ApiKey, User, Webhook, WebhookDelivery
from app.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    WebhookCreate,
    WebhookCreated,
    WebhookDeliveryOut,
    WebhookOut,
    WebhookUpdate,
)
from app.security import new_api_key
from app.services import audit, webhooks
from app.services.net import UnsafeURLError, check_public_url

router = APIRouter(tags=["developer"])


# ---------- API keys ----------
@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(can_admin), db: Session = Depends(get_db)):
    return db.scalars(select(ApiKey).where(ApiKey.org_id == user.org_id).order_by(ApiKey.id.desc())).all()


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_key(body: ApiKeyCreate, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    raw, prefix, key_hash = new_api_key()
    key = ApiKey(org_id=user.org_id, name=body.name, prefix=prefix, key_hash=key_hash, role=body.role,
                 created_by=user.id)
    db.add(key)
    db.commit()
    audit.record(db, user, "api_key.created", target_type="api_key", target_id=key.id,
                 details={"name": key.name, "role": key.role})
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
def revoke_key(key_id: int, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    key = get_owned(db, ApiKey, key_id, user)
    key.revoked_at = key.revoked_at or datetime.now(timezone.utc)
    db.commit()
    audit.record(db, user, "api_key.revoked", target_type="api_key", target_id=key.id, details={"name": key.name})
    return key


# ---------- Webhooks ----------
def _validate_url(url: str) -> None:
    try:
        check_public_url(url)
    except UnsafeURLError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


def _check_events(events: list[str]) -> None:
    unknown = [e for e in events if e not in webhooks.EVENTS and e != "*"]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown event(s): {', '.join(unknown)}")


@router.get("/webhooks/events")
def list_events():
    return webhooks.EVENTS


@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(user: User = Depends(can_admin), db: Session = Depends(get_db)):
    return db.scalars(select(Webhook).where(Webhook.org_id == user.org_id).order_by(Webhook.id)).all()


@router.post("/webhooks", response_model=WebhookCreated, status_code=201)
def create_webhook(body: WebhookCreate, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    _validate_url(body.url)
    _check_events(body.events)
    secret = "whsec_" + secrets.token_urlsafe(24)
    hook = Webhook(org_id=user.org_id, url=body.url, events=body.events, secret=encrypt(secret))
    db.add(hook)
    db.commit()
    audit.record(db, user, "webhook.created", target_type="webhook", target_id=hook.id,
                 details={"url": hook.url, "events": hook.events})
    return WebhookCreated(**WebhookOut.model_validate(hook).model_dump(), secret=secret)


@router.patch("/webhooks/{hook_id}", response_model=WebhookOut)
def update_webhook(hook_id: int, body: WebhookUpdate, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    hook = get_owned(db, Webhook, hook_id, user)
    if body.url is not None:
        _validate_url(body.url)
        hook.url = body.url
    if body.events is not None:
        _check_events(body.events)
        hook.events = body.events
    if body.is_active is not None:
        hook.is_active = body.is_active
    db.commit()
    return hook


@router.delete("/webhooks/{hook_id}", status_code=204)
def delete_webhook(hook_id: int, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    hook = get_owned(db, Webhook, hook_id, user)
    url = hook.url
    db.delete(hook)
    db.commit()
    audit.record(db, user, "webhook.deleted", target_type="webhook", target_id=hook_id, details={"url": url})


@router.post("/webhooks/{hook_id}/test", response_model=WebhookDeliveryOut)
def test_webhook(hook_id: int, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    """Sends a 'ping' event right away (one attempt) and returns the result."""
    hook = get_owned(db, Webhook, hook_id, user)
    delivery = WebhookDelivery(webhook_id=hook.id, event="ping",
                               payload={"event": "ping", "data": {"message": "Test from Inkventory"}})
    db.add(delivery)
    db.commit()
    webhooks.deliver(delivery.id, delays=(0,))
    db.refresh(delivery)
    return delivery


@router.get("/webhooks/{hook_id}/deliveries", response_model=list[WebhookDeliveryOut])
def list_deliveries(hook_id: int, limit: int = Query(20, le=100), user: User = Depends(can_admin),
                    db: Session = Depends(get_db)):
    get_owned(db, Webhook, hook_id, user)
    return db.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook_id)
                      .order_by(WebhookDelivery.id.desc()).limit(limit)).all()
