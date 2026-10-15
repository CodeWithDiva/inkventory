from datetime import datetime
from typing import Literal

from pydantic import computed_field, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services.prompts import LANGUAGES, LENGTHS, TONES


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth & users ----------
class RegisterIn(BaseModel):
    organization_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str = ""
    token_type: str = "bearer"
    # When two-step login is on, sign-in returns mfa_required + mfa_token instead of an access token.
    mfa_required: bool = False
    mfa_token: str | None = None


LoginOut = TokenOut


class MfaVerifyIn(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=20)


class TwoFactorCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class TwoFactorDisableIn(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=10)


class TwoFactorSetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_svg: str


class TwoFactorEnabledOut(BaseModel):
    recovery_codes: list[str]


class DeleteAccountIn(BaseModel):
    password: str


class SessionOut(ORM):
    id: str
    method: str
    ip: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    current: bool = False


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class OrganizationOut(ORM):
    id: int
    name: str
    monthly_generation_limit: int
    plan: str
    plan_status: str


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class MeOut(UserOut):
    organization: OrganizationOut
    is_superadmin: bool = False
    totp_enabled: bool = False
    must_setup_2fa: bool = False


# ---------- Organization settings & billing ----------
class OrgSettingsOut(BaseModel):
    name: str
    has_own_anthropic_key: bool
    anthropic_key_hint: str
    notify_on_job_complete: bool
    require_2fa: bool = False
    approval_steps: int = 1


class OrgSettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    # "" removes the key; None leaves it unchanged
    anthropic_api_key: str | None = Field(default=None, max_length=300)
    notify_on_job_complete: bool | None = None
    require_2fa: bool | None = None
    approval_steps: int | None = Field(default=None, ge=1, le=2)


class PlanOut(BaseModel):
    id: str
    name: str
    price_label: str
    monthly_generations: int
    max_users: int
    engine_kinds: list[str]
    features: list[str]
    can_checkout: bool


class BillingOut(BaseModel):
    plan: PlanOut
    plan_status: str
    plan_renews_at: datetime | None
    billing_provider: str | None
    usage_this_month: int
    monthly_limit: int
    team_members: int
    plans: list[PlanOut]
    billing_contact: str
    requested_plan: str | None = None
    requested_at: datetime | None = None


class CheckoutIn(BaseModel):
    plan: Literal["pro", "business"]


class UpgradeRequestIn(BaseModel):
    plan: Literal["pro", "business"]
    message: str = Field(default="", max_length=1000)


class AdminOrgOut(BaseModel):
    id: int
    name: str
    owner_email: str | None
    plan: str
    plan_status: str
    monthly_generation_limit: int
    usage_this_month: int
    products: int
    members: int
    has_own_anthropic_key: bool
    billing_provider: str | None
    requested_plan: str | None = None
    requested_at: datetime | None = None
    created_at: datetime


class AdminOrgUpdate(BaseModel):
    plan: Literal["free", "pro", "business"] | None = None
    plan_status: Literal["active", "past_due", "cancelled"] | None = None
    monthly_generation_limit: int | None = Field(default=None, ge=0, le=10_000_000)
    plan_renews_at: datetime | None = None


RoleName = Literal["admin", "editor", "reviewer"]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)
    role: RoleName = "editor"


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: RoleName | None = None
    is_active: bool | None = None


# ---------- Catalogs & products ----------
class CatalogPreview(BaseModel):
    headers: list[str]
    sample_rows: list[dict[str, str]]
    row_count: int
    suggested_mapping: dict[str, str]
    product_count: int | None = None  # differs from row_count for Shopify exports (rows per variant/image)
    detected_format: str | None = None


class CatalogOut(ORM):
    id: int
    name: str
    source: str
    source_filename: str | None
    product_count: int
    created_at: datetime


class CatalogCard(CatalogOut):
    """Catalog list entry with what a card needs: a few product photos and progress."""
    thumbnails: list[dict] = []  # {"product_id", "index", "url"}; url is None for uploaded photos
    generated: int = 0
    approved: int = 0
    pending_review: int = 0
    last_job_status: str | None = None


class CatalogStats(BaseModel):
    total_products: int
    by_language: dict[str, dict[str, int]]
    approval_steps: int = 1
    avg_seo_score: float | None


class CatalogDetail(CatalogOut):
    column_mapping: dict
    stats: CatalogStats


class ImportResult(BaseModel):
    catalog: CatalogOut
    imported: int
    updated: int
    errors: list[dict]


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=500)
    category: str | None = None
    brand: str | None = None
    price: str | None = None
    attributes: dict[str, str] = {}
    keywords: list[str] = []
    images: list[str] = Field(default=[], max_length=10, description="Public image URLs")
    original_description: str | None = None

    @field_validator("images")
    @classmethod
    def http_urls(cls, v: list[str]) -> list[str]:
        if any(not u.startswith(("http://", "https://")) for u in v):
            raise ValueError("images must be http(s) URLs")
        return v


class ProductsBulkIn(BaseModel):
    products: list[ProductIn] = Field(min_length=1, max_length=5000)


class CatalogApiCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    products: list[ProductIn] = Field(default=[], max_length=5000)


class ProductUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    brand: str | None = None
    attributes: dict[str, str] | None = None
    keywords: list[str] | None = None


class VersionSummary(ORM):
    id: int
    language: str
    version_no: int
    status: str
    seo_score: int
    title: str
    warnings: list


class ProductOut(ORM):
    id: int
    catalog_id: int
    sku: str
    name: str
    category: str | None
    brand: str | None
    price: str | None
    attributes: dict
    keywords: list
    images: list = []
    original_description: str | None
    assignee_id: int | None = None
    image_analysis: dict | None = None

    @computed_field
    @property
    def photo_analysis_current(self) -> bool:
        """True when the saved photo analysis belongs to the product's current photos."""
        from app.services.vision import cached

        return cached(self.images, self.image_analysis) is not None


class ProductRow(ProductOut):
    current: dict[str, VersionSummary] = {}
    catalog_name: str | None = None


class Page(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


# ---------- Presets ----------
class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tone: str = "professional"
    length: Literal["short", "medium", "long"] = "medium"
    channel: Literal["generic", "shopify", "amazon", "ebay", "daraz"] = "generic"
    instructions: str = Field(default="", max_length=4000)
    examples: list[str] = Field(default=[], max_length=3)

    @field_validator("tone")
    @classmethod
    def known_tone(cls, v: str) -> str:
        if v not in TONES:
            raise ValueError(f"tone must be one of: {', '.join(TONES)}")
        return v


class PresetOut(ORM):
    id: int
    name: str
    tone: str
    length: str
    channel: str = "generic"
    instructions: str
    examples: list
    is_builtin: bool = False


class LearnVoiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    samples: list[str] = Field(default=[], max_length=20)
    catalog_id: int | None = Field(default=None, description="Use existing descriptions from this catalog")
    engine: str | None = None


class LearnVoiceOut(BaseModel):
    preset: "PresetOut"
    summary: str
    samples_used: int


class GlossaryIn(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    kind: Literal["avoid", "keep"]
    replacement: str | None = Field(default=None, max_length=200)


class GlossaryOut(ORM):
    id: int
    term: str
    kind: str
    replacement: str | None


# ---------- Generation ----------
def _check_languages(langs: list[str]) -> list[str]:
    unknown = [l for l in langs if l not in LANGUAGES]
    if unknown:
        raise ValueError(f"Unsupported language(s): {', '.join(unknown)}")
    return list(dict.fromkeys(langs))


class JobCreate(BaseModel):
    catalog_id: int
    preset_id: int | None = None
    engine: str | None = Field(default=None, max_length=120, description="AI engine id; default if omitted")
    languages: list[str] = Field(default=["en"], min_length=1, max_length=10)
    product_ids: list[int] | None = None
    only_missing: bool = False
    extra_instructions: str = Field(default="", max_length=2000)
    variants: int = Field(default=1, ge=1, le=3, description="Alternative versions per product (best becomes current)")
    use_images: bool = True

    @field_validator("languages")
    @classmethod
    def known_languages(cls, v: list[str]) -> list[str]:
        return _check_languages(v)


class JobEstimate(BaseModel):
    items: int
    products_with_images: int = 0
    model: str
    estimated_cost_usd: float
    estimated_minutes: float
    remaining_quota: int


class JobOut(ORM):
    id: int
    catalog_id: int
    preset_id: int | None
    engine: str | None
    variants: int = 1
    use_images: bool = True
    languages: list
    status: str
    total: int
    completed: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    errors: list
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# ---------- Descriptions ----------
class VersionOut(ORM):
    id: int
    product_id: int
    job_id: int | None
    language: str
    version_no: int
    is_current: bool
    source: str
    title: str
    short_description: str
    long_description: str
    bullet_points: list
    meta_title: str
    meta_description: str
    seo_score: int
    seo_report: dict
    warnings: list
    status: str
    review_note: str | None
    reviewed_at: datetime | None
    model: str | None
    input_tokens: int
    output_tokens: int
    created_at: datetime


class VersionEdit(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    short_description: str
    long_description: str
    bullet_points: list[str]
    meta_title: str = Field(max_length=255)
    meta_description: str = Field(max_length=500)


class ReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class BulkReviewIn(BaseModel):
    version_ids: list[int] = Field(min_length=1, max_length=5000)
    action: Literal["approve", "reject"]
    note: str | None = None


class ApproveDraftsIn(BaseModel):
    language: str = "en"
    min_seo_score: int = Field(default=0, ge=0, le=100)
    skip_flagged: bool = True


class RegenerateIn(BaseModel):
    language: str = "en"
    preset_id: int | None = None
    engine: str | None = Field(default=None, max_length=120)
    use_images: bool = True
    extra_instructions: str = Field(default="", max_length=2000)

    @field_validator("language")
    @classmethod
    def known_language(cls, v: str) -> str:
        return _check_languages([v])[0]


# ---------- Integrations & exports ----------
class IntegrationIn(BaseModel):
    platform: Literal["shopify", "woocommerce"]
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, str]


class IntegrationOut(ORM):
    id: int
    platform: str
    name: str
    config: dict
    created_at: datetime
    sync_catalog_id: int | None = None
    auto_sync_hours: int | None = None
    last_synced_at: datetime | None = None


class ExportCreate(BaseModel):
    catalog_id: int
    language: str = "en"
    format: Literal["csv_generic", "csv_shopify", "csv_woocommerce", "csv_amazon", "csv_ebay", "csv_daraz",
                    "shopify", "woocommerce"]
    integration_id: int | None = None


class ExportOut(ORM):
    id: int
    catalog_id: int
    integration_id: int | None
    format: str
    language: str
    status: str
    total: int
    succeeded: int
    failed: int
    errors: list
    file_key: str | None
    created_at: datetime
    finished_at: datetime | None


class EngineOut(BaseModel):
    id: str
    label: str
    kind: Literal["free", "cloud", "local", "demo"]
    free: bool
    available: bool
    note: str
    needs_upgrade: bool = False


class OptionsOut(BaseModel):
    languages: dict[str, str]
    tones: dict[str, str]
    lengths: dict[str, list[int]]
    llm_provider: str
    llm_model: str
    default_engine: str
    engines: list[EngineOut]
    channels: dict[str, str]

    @classmethod
    def build(cls, provider: str, model: str, default_engine: str, engines: list[dict]) -> "OptionsOut":
        from app.services.channels import CHANNELS

        return cls(languages=LANGUAGES, tones=TONES,
                   lengths={k: list(v) for k, v in LENGTHS.items()},
                   llm_provider=provider, llm_model=model,
                   default_engine=default_engine, engines=engines,
                   channels={k: c.name for k, c in CHANNELS.items()})


# ---------- Developer: API keys & webhooks ----------
class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "editor", "reviewer"] = "editor"


class ApiKeyOut(ORM):
    id: int
    name: str
    prefix: str
    role: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str


class WebhookCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    events: list[str] = Field(min_length=1)


class WebhookUpdate(BaseModel):
    url: str | None = Field(default=None, max_length=1000)
    events: list[str] | None = None
    is_active: bool | None = None


class WebhookOut(ORM):
    id: int
    url: str
    events: list
    is_active: bool
    created_at: datetime


class WebhookCreated(WebhookOut):
    secret: str


class WebhookDeliveryOut(ORM):
    id: int
    event: str
    attempts: int
    status_code: int | None
    success: bool
    error: str | None
    created_at: datetime
    delivered_at: datetime | None


# ---------- Store sync & URL import ----------
class SyncStart(BaseModel):
    catalog_id: int | None = Field(default=None, description="Existing catalog to update; a new one is created if empty")


class SyncSettings(BaseModel):
    auto_sync_hours: int | None = Field(default=None, ge=1, le=24 * 7)
    sync_catalog_id: int | None = None


class SyncRunOut(ORM):
    id: int
    integration_id: int
    catalog_id: int | None
    trigger: str
    status: str
    imported: int
    updated: int
    errors: list
    created_at: datetime
    finished_at: datetime | None


class UrlImportIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    name: str = Field(default="Imported catalog", max_length=200)
    mapping: dict[str, str] | None = None
    catalog_id: int | None = None

class AuditLogOut(ORM):
    id: int
    user_id: int | None
    actor: str
    action: str
    target_type: str | None
    target_id: str | None
    details: dict
    ip: str | None
    created_at: datetime


class DeleteOrgIn(BaseModel):
    organization_name: str
    password: str

# ---------- Workflow ----------
class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    language: str | None = None
    version_id: int | None = None
    mentions: list[int] = Field(default=[], max_length=20)


class CommentOut(ORM):
    id: int
    product_id: int
    language: str | None
    version_id: int | None
    user_id: int | None
    author: str
    body: str
    mentions: list
    resolved: bool
    created_at: datetime


class AssignIn(BaseModel):
    user_id: int | None


class BulkAssignIn(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=5000)
    user_id: int | None


class NotificationOut(ORM):
    id: int
    kind: str
    title: str
    body: str
    link: str | None
    read_at: datetime | None
    created_at: datetime


class ReadNotificationsIn(BaseModel):
    ids: list[int] | None = None  # None = all