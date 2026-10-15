"""Platform admin (the SaaS owner): every organization, its usage, and manual plan changes.

Access is limited to emails listed in SUPERADMIN_EMAILS.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_owned, require_superadmin
from app.models import Organization, Product, Role, User
from app.routers.billing import apply_plan
from app.schemas import AdminOrgOut, AdminOrgUpdate
from app.services import audit
from app.services.generation import monthly_usage

router = APIRouter(prefix="/admin", tags=["platform admin"])


def _org_out(db: Session, org: Organization) -> AdminOrgOut:
    owner = db.scalar(select(User.email).where(User.org_id == org.id, User.role == Role.OWNER))
    return AdminOrgOut(
        id=org.id,
        name=org.name,
        owner_email=owner,
        plan=org.plan,
        plan_status=org.plan_status,
        monthly_generation_limit=org.monthly_generation_limit,
        usage_this_month=monthly_usage(db, org.id),
        products=db.scalar(select(func.count(Product.id)).where(Product.org_id == org.id)) or 0,
        members=db.scalar(select(func.count(User.id)).where(User.org_id == org.id)) or 0,
        has_own_anthropic_key=bool(org.anthropic_api_key),
        billing_provider=org.billing_provider,
        requested_plan=org.requested_plan,
        requested_at=org.requested_at,
        created_at=org.created_at,
    )


@router.get("/organizations", response_model=list[AdminOrgOut])
def list_organizations(admin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    orgs = db.scalars(select(Organization).order_by(Organization.id.desc())).all()
    return [_org_out(db, o) for o in orgs]


@router.patch("/organizations/{org_id}", response_model=AdminOrgOut)
def update_organization(
    org_id: int, body: AdminOrgUpdate, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)
):
    org = db.get(Organization, org_id)
    if org is None:
        get_owned(db, Organization, org_id, admin)  # raises 404
    if body.plan is not None:
        apply_plan(org, body.plan)
        org.billing_provider = org.billing_provider or "manual"
    if body.plan_status is not None:
        org.plan_status = body.plan_status
    if body.monthly_generation_limit is not None:  # custom deals
        org.monthly_generation_limit = body.monthly_generation_limit
    if body.plan_renews_at is not None:
        org.plan_renews_at = body.plan_renews_at
    db.commit()
    audit.record(db, admin, "billing.plan_changed", org_id=org.id, target_type="organization", target_id=org.id,
                 details={"by": "platform admin", **body.model_dump(exclude_none=True, mode="json")})
    return _org_out(db, org)
