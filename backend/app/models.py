"""Database models.

Every tenant-owned row carries ``org_id`` so each customer organization only ever
sees its own catalogs, products, descriptions and integrations.
"""

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"  # first approval given; waiting for final approval (2-step workflow)
    APPROVED = "approved"
    REJECTED = "rejected"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    monthly_generation_limit: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Billing
    plan: Mapped[str] = mapped_column(String(30), default="free", server_default="free")
    plan_status: Mapped[str] = mapped_column(String(30), default="active", server_default="active")
    plan_renews_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billing_provider: Mapped[str | None] = mapped_column(String(30))  # lemonsqueezy | manual
    billing_customer_id: Mapped[str | None] = mapped_column(String(120))
    billing_subscription_id: Mapped[str | None] = mapped_column(String(120), index=True)
    # Upgrade asked for by a customer paying by bank transfer / invoice (cleared on plan change)
    requested_plan: Mapped[str | None] = mapped_column(String(30))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The organization's own Claude key (encrypted). When set, their cloud jobs bill to them.
    anthropic_api_key: Mapped[str | None] = mapped_column(Text)
    notify_on_job_complete: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    require_2fa: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 1 = a reviewer approves; 2 = reviewer approves, then an admin gives final approval
    approval_steps: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    users: Mapped[list["User"]] = relationship(back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.EDITOR)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Bumped on password change; tokens issued before it are rejected.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Two-step login (TOTP authenticator app)
    totp_secret: Mapped[str | None] = mapped_column(Text)  # encrypted
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    recovery_codes: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")  # sha256 hashes
    sso_provider: Mapped[str | None] = mapped_column(String(30))  # google | microsoft (last used)

    organization: Mapped[Organization] = relationship(back_populates="users")


class Catalog(Base):
    __tablename__ = "catalogs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="csv")  # csv | api
    source_filename: Mapped[str | None] = mapped_column(String(255))
    file_key: Mapped[str | None] = mapped_column(String(500))
    column_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    products: Mapped[list["Product"]] = relationship(
        back_populates="catalog", cascade="all, delete-orphan"
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("catalog_id", "sku", name="uq_product_catalog_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    catalog_id: Mapped[int] = mapped_column(ForeignKey("catalogs.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(255))
    price: Mapped[str | None] = mapped_column(String(50))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    # Image URLs (http/https) or uploaded files ("storage:<key>"), used by vision-capable AI engines
    images: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # What a vision model saw in the photos (see services/vision.py); refreshed when the photos change.
    image_analysis: Mapped[dict | None] = mapped_column(JSON)
    original_description: Mapped[str | None] = mapped_column(Text)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    catalog: Mapped[Catalog] = relationship(back_populates="products")
    versions: Mapped[list["DescriptionVersion"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class StylePreset(Base):
    """Tone & style settings. ``org_id`` NULL means a built-in preset shared by all tenants."""

    __tablename__ = "style_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    tone: Mapped[str] = mapped_column(String(50))
    length: Mapped[str] = mapped_column(String(20), default="medium")  # short | medium | long
    channel: Mapped[str] = mapped_column(String(30), default="generic", server_default="generic")
    instructions: Mapped[str] = mapped_column(Text, default="")
    examples: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GlossaryTerm(Base):
    """Company terminology: words to avoid (optionally auto-replaced) and terms to keep exactly."""

    __tablename__ = "glossary_terms"
    __table_args__ = (UniqueConstraint("org_id", "term", name="uq_glossary_org_term"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    term: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))  # avoid | keep
    replacement: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    catalog_id: Mapped[int] = mapped_column(ForeignKey("catalogs.id", ondelete="CASCADE"), index=True)
    preset_id: Mapped[int | None] = mapped_column(ForeignKey("style_presets.id", ondelete="SET NULL"))
    engine: Mapped[str | None] = mapped_column(String(120))  # AI engine id; NULL = default
    languages: Mapped[list] = mapped_column(JSON, default=list)
    product_ids: Mapped[list | None] = mapped_column(JSON)
    only_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    variants: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    use_images: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    extra_instructions: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DescriptionVersion(Base):
    """One generated (or manually edited) description. Rows are never overwritten,
    which gives full version history; ``is_current`` marks the version under review."""

    __tablename__ = "description_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"))
    language: Mapped[str] = mapped_column(String(10), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="ai")  # ai | manual | restore

    title: Mapped[str] = mapped_column(String(500))
    short_description: Mapped[str] = mapped_column(Text)
    long_description: Mapped[str] = mapped_column(Text)
    bullet_points: Mapped[list] = mapped_column(JSON, default=list)
    meta_title: Mapped[str] = mapped_column(String(255))
    meta_description: Mapped[str] = mapped_column(String(500))

    seo_score: Mapped[int] = mapped_column(Integer, default=0)
    seo_report: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(String(20), default=ReviewStatus.DRAFT, index=True)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    first_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model: Mapped[str | None] = mapped_column(String(100))
    preset_id: Mapped[int | None] = mapped_column(ForeignKey("style_presets.id", ondelete="SET NULL"))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    product: Mapped[Product] = relationship(back_populates="versions")


class Integration(Base):
    __tablename__ = "integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))  # shopify | woocommerce
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Product import / scheduled sync from the store into a catalog
    sync_catalog_id: Mapped[int | None] = mapped_column(ForeignKey("catalogs.id", ondelete="SET NULL"))
    auto_sync_hours: Mapped[int | None] = mapped_column(Integer)  # None = manual only
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    catalog_id: Mapped[int] = mapped_column(ForeignKey("catalogs.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int | None] = mapped_column(ForeignKey("integrations.id", ondelete="SET NULL"))
    format: Mapped[str] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING)
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    file_key: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    """One product import from a connected store into a catalog."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), index=True)
    catalog_id: Mapped[int | None] = mapped_column(ForeignKey("catalogs.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | schedule | api
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    """Key for programmatic access. Only a SHA-256 hash is stored; the key is shown once."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default=Role.EDITOR)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Webhook(Base):
    """Outgoing event notifications to a customer's URL, signed with HMAC-SHA256."""

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    events: Mapped[list] = mapped_column(JSON, default=list)
    secret: Mapped[str] = mapped_column(Text)  # encrypted
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class UserSession(Base):
    """A signed-in device. Access tokens carry the session id, so a session can be revoked remotely."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    method: Mapped[str] = mapped_column(String(20), default="password")  # password | 2fa | sso:google | reset
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Who did what, when and from where. Append-only."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(255))  # email or "API key: name" or "system"
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

class Comment(Base):
    """Discussion on a product's description, with @mentions."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    language: Mapped[str | None] = mapped_column(String(10))
    version_id: Mapped[int | None] = mapped_column(ForeignKey("description_versions.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list] = mapped_column(JSON, default=list)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(500))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)