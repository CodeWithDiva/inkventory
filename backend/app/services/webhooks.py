"""Outgoing webhooks: signed JSON POSTs to customer URLs when things happen in their workspace.

Each request carries:
  X-Inkventory-Event:      event name, e.g. "job.completed"
  X-Inkventory-Delivery:   delivery id (use it to ignore duplicates)
  X-Inkventory-Signature:  "sha256=" + HMAC-SHA256(secret, raw body) - verify it before trusting the call
Failed deliveries are retried with backoff (3 attempts).
"""

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import decrypt
from app.db import SessionLocal
from app.models import Webhook, WebhookDelivery

log = logging.getLogger(__name__)

EVENTS = {
    "catalog.imported": "A catalog was uploaded or synced from a store",
    "job.completed": "A generation job finished",
    "job.failed": "A generation job failed",
    "description.approved": "A description was approved",
    "description.rejected": "A description was rejected",
    "export.completed": "An export or store push finished",
}
RETRY_DELAYS = (0, 5, 30)  # seconds before attempt 1, 2, 3
TIMEOUT = 10.0

# Overridable in tests.
transport: httpx.BaseTransport | None = None


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def emit(db: Session, org_id: int, event: str, data: dict) -> None:
    """Queue a delivery for every active webhook of the organization subscribed to ``event``."""
    hooks = db.scalars(select(Webhook).where(Webhook.org_id == org_id, Webhook.is_active.is_(True))).all()
    ids = []
    for hook in hooks:
        if event not in (hook.events or []) and "*" not in (hook.events or []):
            continue
        payload = {"event": event, "created_at": datetime.now(timezone.utc).isoformat(), "data": data}
        delivery = WebhookDelivery(webhook_id=hook.id, event=event, payload=payload)
        db.add(delivery)
        db.flush()
        ids.append(delivery.id)
    if not ids:
        return
    db.commit()
    for delivery_id in ids:
        if get_settings().task_mode == "sync":
            deliver(delivery_id, delays=(0,))
        else:
            threading.Thread(target=deliver, args=(delivery_id,), daemon=True).start()


def deliver(delivery_id: int, delays: tuple[int, ...] = RETRY_DELAYS) -> bool:
    with SessionLocal() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        hook = db.get(Webhook, delivery.webhook_id) if delivery else None
        if delivery is None or hook is None:
            return False
        body = json.dumps(delivery.payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Inkventory-Webhooks/1.0",
            "X-Inkventory-Event": delivery.event,
            "X-Inkventory-Delivery": str(delivery.id),
            "X-Inkventory-Signature": sign(decrypt(hook.secret), body),
        }
        with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
            for delay in delays:
                if delay:
                    time.sleep(delay)
                delivery.attempts += 1
                try:
                    response = client.post(hook.url, content=body, headers=headers)
                    delivery.status_code = response.status_code
                    delivery.success = 200 <= response.status_code < 300
                    delivery.error = None if delivery.success else response.text[:300]
                except httpx.HTTPError as exc:
                    delivery.error = f"{type(exc).__name__}: {exc}"[:300]
                db.commit()
                if delivery.success:
                    delivery.delivered_at = datetime.now(timezone.utc)
                    db.commit()
                    return True
        log.warning("Webhook delivery %s to %s failed: %s", delivery.id, hook.url, delivery.error)
        return False


def safe_emit(db: Session, org_id: int, event: str, data: dict) -> None:
    """Webhook problems must never break the action that triggered them."""
    try:
        emit(db, org_id, event, data)
    except Exception:
        log.exception("Could not queue webhook %s for org %s", event, org_id)
