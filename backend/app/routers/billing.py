"""Plans, checkout and the Lemon Squeezy subscription webhook.

Customers who pay by bank transfer / invoice are upgraded by a platform admin (see admin.py).
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import can_admin, get_current_user
from app.models import Organization, User
from app.plans import PLANS, Plan, get_plan
from app.schemas import BillingOut, CheckoutIn, PlanOut, UpgradeRequestIn
from app.services import audit, email
from app.services.generation import monthly_usage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


def checkout_url_for(plan_id: str) -> str:
    settings = get_settings()
    return {"pro": settings.lemonsqueezy_checkout_pro, "business": settings.lemonsqueezy_checkout_business}.get(plan_id, "")


def plan_for_variant(variant_id: str) -> str | None:
    settings = get_settings()
    mapping = {settings.lemonsqueezy_variant_pro: "pro", settings.lemonsqueezy_variant_business: "business"}
    return mapping.get(str(variant_id)) if variant_id else None


def plan_out(plan: Plan) -> PlanOut:
    return PlanOut(
        id=plan.id, name=plan.name, price_label=plan.price_label, monthly_generations=plan.monthly_generations,
        max_users=plan.max_users, engine_kinds=list(plan.engine_kinds), features=list(plan.features),
        can_checkout=bool(checkout_url_for(plan.id)),
    )


def apply_plan(org: Organization, plan_id: str) -> None:
    """Switch plan and reset the monthly limit to the plan's allowance."""
    plan = get_plan(plan_id)
    org.plan = plan.id
    org.monthly_generation_limit = plan.monthly_generations
    org.requested_plan = None
    org.requested_at = None


@router.get("", response_model=BillingOut)
def billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.get(Organization, user.org_id)
    members = db.scalar(select(func.count(User.id)).where(User.org_id == org.id, User.is_active.is_(True))) or 0
    return BillingOut(
        plan=plan_out(get_plan(org.plan)),
        plan_status=org.plan_status,
        plan_renews_at=org.plan_renews_at,
        billing_provider=org.billing_provider,
        usage_this_month=monthly_usage(db, org.id),
        monthly_limit=org.monthly_generation_limit,
        team_members=members,
        plans=[plan_out(p) for p in PLANS.values()],
        billing_contact=get_settings().billing_contact,
        requested_plan=org.requested_plan,
        requested_at=org.requested_at,
    )


@router.post("/request-upgrade")
def request_upgrade(body: UpgradeRequestIn, user: User = Depends(can_admin), db: Session = Depends(get_db)):
    """For customers paying by bank transfer / invoice: notify the platform owners."""
    org = db.get(Organization, user.org_id)
    if body.plan == org.plan:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You are already on this plan")
    org.requested_plan = body.plan
    org.requested_at = datetime.now(timezone.utc)
    db.commit()
    plan = get_plan(body.plan)
    for admin_email in get_settings().superadmins:
        email.upgrade_requested(admin_email, org.name, user.full_name, user.email, plan.name, body.message)
    return {"message": f"Thanks! We received your request for the {plan.name} plan and will contact you at "
                       f"{user.email} with payment details."}


@router.post("/checkout")
def checkout(body: CheckoutIn, user: User = Depends(can_admin)):
    base = checkout_url_for(body.plan)
    if not base:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Online payment is not set up yet. Please contact us to upgrade by bank transfer or invoice.",
        )
    # org_id travels through checkout and comes back in the webhook's custom data.
    query = urlencode({"checkout[custom][org_id]": str(user.org_id), "checkout[email]": user.email})
    return {"url": f"{base}{'&' if '?' in base else '?'}{query}"}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.post("/webhook/lemonsqueezy", include_in_schema=False)
async def lemonsqueezy_webhook(request: Request, db: Session = Depends(get_db)):
    secret = get_settings().lemonsqueezy_webhook_secret
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(expected, request.headers.get("X-Signature", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")

    payload = json.loads(raw)
    event = payload.get("meta", {}).get("event_name", "")
    if not event.startswith("subscription_"):
        return {"ignored": event}
    data = payload.get("data", {})
    attrs = data.get("attributes", {})
    subscription_id = str(data.get("id", ""))

    org_id = (payload.get("meta", {}).get("custom_data") or {}).get("org_id")
    org = db.get(Organization, int(org_id)) if org_id else None
    if org is None and subscription_id:
        org = db.scalar(select(Organization).where(Organization.billing_subscription_id == subscription_id))
    if org is None:
        log.warning("Lemon Squeezy %s for unknown organization (subscription %s)", event, subscription_id)
        return {"ignored": "unknown organization"}

    org.billing_provider = "lemonsqueezy"
    org.billing_subscription_id = subscription_id
    org.billing_customer_id = str(attrs.get("customer_id") or org.billing_customer_id or "")
    org.plan_renews_at = _parse_time(attrs.get("renews_at") or attrs.get("ends_at"))
    sub_status = attrs.get("status", "")

    if sub_status in ("active", "on_trial"):
        plan_id = plan_for_variant(str(attrs.get("variant_id", "")))
        if plan_id:
            apply_plan(org, plan_id)
        org.plan_status = "active"
    elif sub_status == "cancelled":
        org.plan_status = "cancelled"  # keeps the paid plan until ends_at, then "expired" arrives
    elif sub_status in ("past_due", "unpaid"):
        org.plan_status = "past_due"
    elif sub_status == "expired":
        apply_plan(org, "free")
        org.plan_status = "active"
    db.commit()
    audit.record(db, None, "billing.plan_changed", org_id=org.id, target_type="organization", target_id=org.id,
                 details={"by": "lemonsqueezy", "event": event, "plan": org.plan, "status": org.plan_status})
    log.info("Lemon Squeezy %s: org %s -> plan %s (%s)", event, org.id, org.plan, org.plan_status)
    return {"ok": True}
