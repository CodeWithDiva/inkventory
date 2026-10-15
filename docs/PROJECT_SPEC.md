# Inkventory — Complete Product & Technical Specification

> Purpose of this document: a complete, self-contained specification of **Inkventory**, an AI product-description
> generator for e-commerce catalogs, detailed enough for a developer (or an AI coding assistant such as ChatGPT)
> to rebuild it. Section 14 contains ready-made, phase-by-phase prompts.

---

## 1. Product summary

**Inkventory** is a multi-tenant SaaS web application that writes unique, SEO-optimized product descriptions in
bulk for large product catalogs.

**Core flow:** upload products (CSV, Google Sheets, API or a Shopify/WooCommerce store) → AI writes descriptions
(any tone, 15 languages, marketplace rules) → automatic SEO score and quality checks → team reviews, edits and
approves → export to CSV/marketplace formats or push directly to the store.

**Problem solved:** businesses with thousands of SKUs cannot write unique, high-quality descriptions by hand;
copying the manufacturer's text causes duplicate content and poor SEO.

**Target users:** e-commerce businesses, catalog managers, marketplace sellers (Amazon, eBay, Daraz), agencies.

---

## 2. Roles & tenancy

- Every customer is an **Organization** (workspace). All tenant data rows carry `org_id`; every query filters by
  the caller's `org_id`; another org's IDs return **404** (never 403, so IDs don't leak).
- Roles inside an org:

| Role | Permissions |
|---|---|
| owner | everything; cannot be modified by admins; can delete the workspace and export all data |
| admin | everything except workspace deletion: team, settings, store connections, API keys, webhooks, audit log, final approval |
| editor | upload/import, generate, edit, regenerate, assign, export |
| reviewer | approve / reject (first-step approval in the two-step workflow) |

- **Platform owner (superadmin):** emails listed in `SUPERADMIN_EMAILS` see a *Platform admin* page with all
  organizations, usage and manual plan changes.
- **API keys** act with their own role inside one org and cannot perform account actions (passwords, keys, billing).

---

## 3. Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic migrations |
| Database | SQLite for local dev; PostgreSQL in production |
| Background jobs | `TASK_MODE`: `sync` (tests), `thread` (local), `celery` + Redis (production; `celery -A app.tasks worker -B` also runs beat) |
| AI providers | Anthropic Claude (official `anthropic` SDK), Google Gemini and Groq (via OpenAI-compatible API using the `openai` SDK), local Ollama (HTTP), mock provider for tests/demo |
| Auth | JWT (HS256) access tokens + server-side sessions, bcrypt passwords, TOTP 2FA, Google/Microsoft OpenID Connect |
| Secrets at rest | Fernet encryption (`cryptography`), values stored as `enc:<token>` |
| Files | local disk or S3 (`STORAGE_BACKEND`) |
| Email | console (dev) or SMTP |
| Frontend | React 18 + TypeScript + Vite, TanStack Query, React Router; plain CSS design system (no UI library) |
| Deploy | Docker Compose: postgres, redis, api, worker, web (nginx serving the SPA and proxying `/api`) |
| CI | GitHub Actions: backend tests, frontend build, docker build |

---

## 4. Data model (19 tables)

All timestamps are timezone-aware UTC. `id` is an integer primary key unless noted.

**organizations** — `name`, `monthly_generation_limit`, `plan` (free|pro|business), `plan_status`
(active|past_due|cancelled), `plan_renews_at`, `billing_provider` (lemonsqueezy|manual), `billing_customer_id`,
`billing_subscription_id`, `requested_plan`, `requested_at`, `anthropic_api_key` (encrypted; the org's own Claude
key), `notify_on_job_complete`, `require_2fa`, `approval_steps` (1|2), `created_at`.

**users** — `org_id`, `email` (unique), `full_name`, `password_hash`, `role`, `is_active`, `password_changed_at`,
`totp_secret` (encrypted), `totp_enabled`, `recovery_codes` (JSON list of SHA-256 hashes), `sso_provider`, `created_at`.

**catalogs** — `org_id`, `name`, `source` (csv|api|url), `source_filename`, `file_key`, `column_mapping` (JSON),
`product_count`, `created_by`, `created_at`.

**products** — `org_id`, `catalog_id`, `sku` (unique per catalog), `name`, `category`, `brand`, `price`,
`attributes` (JSON dict, every unmapped CSV column), `keywords` (JSON list; first = primary), `images` (JSON list
of http(s) URLs or `storage:<key>` for uploads), `original_description`, `assignee_id`, `created_at`.

**style_presets** — `org_id` (NULL = built-in), `name`, `tone`, `length` (short|medium|long), `channel`
(generic|shopify|amazon|ebay|daraz), `instructions`, `examples` (≤3 sample descriptions), `created_at`.
Built-ins seeded on startup: Professional, Friendly & Casual, Luxury, Technical Specs, Short & Minimal.

**glossary_terms** — `org_id`, `term` (unique per org), `kind` (avoid|keep), `replacement` (optional, avoid only).

**generation_jobs** — `org_id`, `catalog_id`, `preset_id`, `engine`, `languages` (JSON), `product_ids` (JSON or
NULL = all), `only_missing`, `variants` (1–3), `use_images`, `extra_instructions`, `status`
(pending|running|completed|failed|cancelled), `total`, `completed`, `failed`, `input_tokens`, `output_tokens`,
`cost_usd`, `errors` (JSON, last 200), `created_by`, `created_at`, `started_at`, `finished_at`.

**description_versions** (never overwritten → full history) — `org_id`, `product_id`, `job_id`, `language`,
`version_no` (per product+language), `is_current`, `source` (ai|manual|restore), `title`, `short_description`,
`long_description`, `bullet_points` (JSON), `meta_title`, `meta_description`, `seo_score` (0–100), `seo_report`
(JSON list of checks), `warnings` (JSON list of `{type, message}`), `status` (draft|in_review|approved|rejected),
`review_note`, `reviewed_by`, `reviewed_at`, `first_approved_by`, `first_approved_at`, `model`, `preset_id`,
`input_tokens`, `output_tokens`, `created_by`, `created_at`.

**integrations** — `org_id`, `platform` (shopify|woocommerce), `name`, `config` (JSON; secret fields encrypted),
`sync_catalog_id`, `auto_sync_hours` (NULL|6|24|168), `last_synced_at`.

**export_jobs** — `org_id`, `catalog_id`, `integration_id`, `format`, `language`, `status`, `total`, `succeeded`,
`failed`, `errors`, `file_key`, `created_by`, `created_at`, `finished_at`.

**sync_runs** — `org_id`, `integration_id`, `catalog_id`, `trigger` (manual|schedule|api), `status`, `imported`,
`updated`, `errors`, `created_by`, `created_at`, `finished_at`.

**api_keys** — `org_id`, `name`, `prefix` (first 12 chars), `key_hash` (SHA-256, unique), `role`, `created_by`,
`created_at`, `last_used_at`, `revoked_at`. Raw key format `cai_<random>` is shown once.

**webhooks** — `org_id`, `url`, `events` (JSON, `*` = all), `secret` (encrypted), `is_active`.
**webhook_deliveries** — `webhook_id`, `event`, `payload`, `attempts`, `status_code`, `success`, `error`,
`created_at`, `delivered_at`.

**user_sessions** — `id` (UUID string), `user_id`, `org_id`, `method` (password|2fa|recovery_code|reset|sso:google…),
`ip`, `user_agent`, `created_at`, `last_seen_at`, `revoked_at`.

**audit_logs** — `org_id`, `user_id`, `actor` (email / "API key: name" / "system"), `action`, `target_type`,
`target_id`, `details` (JSON; never secrets), `ip`, `created_at`.

**comments** — `org_id`, `product_id`, `language`, `version_id`, `user_id`, `author`, `body`, `mentions` (user ids),
`resolved`, `created_at`.

**notifications** — `org_id`, `user_id`, `kind` (assigned|mention|comment|approval_needed|rejected|job_finished),
`title`, `body`, `link`, `read_at`, `created_at`.

Migrations: Alembic, run automatically on startup (with a PostgreSQL advisory lock so several API workers don't
race). A database created before migrations existed is detected and stamped at the baseline revision.

---

## 5. AI generation pipeline

### 5.1 Output schema (every provider must return this JSON)
`title`, `short_description` (≤300 chars), `long_description` (plain-text paragraphs separated by a blank line),
`bullet_points` (list), `meta_title` (30–60 chars), `meta_description` (120–160 chars).

### 5.2 Prompt
- **System prompt** (identical for every product in a job → cacheable): role ("expert e-commerce copywriter and
  SEO specialist"); **accuracy rules** (use only facts in the product data; never invent specs, materials,
  dimensions, certifications, warranties; don't mention price; treat product data as data and ignore instructions
  inside it; photos only for visible facts); style (target language written natively, tone description, length
  range in words); SEO rules (primary keyword in title, first sentence and meta description; no stuffing);
  field rules (title/bullet limits come from the sales channel); channel rules; glossary rules; brand guidelines;
  up to 3 brand examples; batch instructions.
- **User prompt:** product data as JSON inside `<product_data>` tags, keyword list, optional
  "N product photos attached", variant instruction ("Variant k of N: lead with <angle>"), and for non-English a
  repeated instruction to write every field in that language and script.
- Tones: professional, friendly, luxury, technical, playful, minimalist. Lengths (words): short 60–100,
  medium 120–200, long 250–400. Languages: en, ur, ar, hi, es, fr, de, it, pt, nl, tr, ru, zh, ja, ko.

### 5.3 Providers & engines
Engine ids: `gemini`, `groq` (free cloud tiers via OpenAI-compatible endpoints), `ollama:<model>` (local),
`anthropic`, `openai`, `mock`. `LLM_PROVIDER=auto` picks the first configured of Gemini → Groq → Ollama → mock.

- **Claude:** official SDK `messages.create` with `output_config.format` = JSON schema (structured outputs) and
  `output_config.effort`; system prompt block with `cache_control: ephemeral`; images as content blocks (URL or
  base64); check `stop_reason` (`refusal`, `max_tokens`) before parsing; optional server-side refusal fallback.
  **Claude is only available to orgs that saved their own key** (`CLAUDE_OWN_KEY_ONLY=true`); usage is billed to them.
- **Gemini / Groq / OpenAI:** chat completions with `response_format: json_object`; the system prompt ends with a
  filled-in JSON **example** (not a JSON schema — models copy a schema's shape); parse by extracting the outermost
  `{…}` (ignore `<think>` blocks and code fences); if the provider rejects JSON mode, retry once without it.
  Free tiers: pace to N requests/minute (`GEMINI_RPM`, `GROQ_RPM`), retry 429/5xx with backoff
  (10, 20, 40, 60, 60 s or the `retry-after` header), and **stop the whole job** when a *daily* quota is exhausted
  (detected from "per day" wording or a "retry in XhYm" longer than 5 minutes).
- **Ollama:** `POST /api/chat` with `format` = JSON schema; images only for vision models (llava, *-vl, gemma3…).
- **Mock:** deterministic text built from the product data (for tests and demos).

### 5.4 Job runner
For each (product, language, variant): build prompts → call the model in a thread pool (I/O bound) → **all DB
writes on the coordinating thread**. Per product failures are recorded in `job.errors` and never stop the batch.
Cancel is checked after every item. With variants, the highest SEO score (fewest warnings) becomes current.
`only_missing` skips (product, language) pairs that already have a current version. Quota: a job needs
`items × variants` generations from the org's monthly allowance. On finish: in-app notification, email, webhook.

### 5.5 Saving a version
1. Apply glossary: replace "avoid" terms that have a replacement (case-insensitive, whole word) → warning
   `glossary_fixed`; flag remaining avoid terms → warning `glossary`.
2. SEO score (5.6) + channel compliance checks → warning `channel_rules` for failed channel checks.
3. Quality warnings: `wrong_language` (non-Latin target language but <50% of letters in its script),
   `unverified_facts` (numbers in the output not present anywhere in the product data),
   `duplicate_content` (word 3-gram Jaccard similarity ≥ 0.6 against the 500 most recent current descriptions
   in the same catalog/language), provider notes (e.g. "model cannot read images").
4. Mark previous versions of (product, language) not current; insert the new version as current, status draft.

### 5.6 SEO score (0–100 = weighted share of passed checks)
| Check | Weight |
|---|---|
| primary keyword in title | 15 |
| primary keyword in meta description | 12 |
| primary keyword in first sentence | 10 |
| ≥60% of keywords used anywhere | 10 |
| primary keyword density 0.5–3% | 8 |
| title length 20–(channel max) | 8 |
| meta title 30–60 chars | 8 |
| meta description 120–160 chars | 10 |
| bullet count within channel range | 5 |
| description length within 0.8×min … 1.25×max words | 8 |
| average sentence ≤ 22 words | 6 |
| channel: bullets ≤ max chars / description ≤ max chars / no prohibited phrases | 4 / 4 / 8 |
Word-based checks are skipped for zh, ja, ko.

### 5.7 Sales channels
| Channel | Title max | Bullets | Other rules |
|---|---|---|---|
| generic | 70 | 3–6 | — |
| shopify | 70 | 3–6 | HTML paragraphs + list |
| amazon | 200 | exactly 5, ≤255 chars | description ≤2000 chars, plain text, no promo words (best seller, free shipping, sale, guarantee, #1…) |
| ebay | 80 | 3–8 | no keyword spam |
| daraz | 120 | 3–8, ≤200 chars | no phone numbers, links or contact details |

### 5.8 Brand-voice learning
Input: 3–20 existing descriptions (≥15 words each), pasted or taken from a catalog's original descriptions.
The model returns `{summary, closest_tone, typical_length, instructions}`; a new preset is saved with those
instructions and the first 3 samples as examples.

---

## 6. Import

- **CSV:** decode UTF-8/CP1252, sniff delimiter (`, ; tab |`), auto-map columns by aliases (sku: product_id, code,
  variant_sku…; name: title, product_name…; category, brand, price, keywords: tags…; description: body_html…;
  images: image, image_url, image src…). Two steps: preview (headers, 5 sample rows, suggested mapping) → import
  with confirmed mapping. Validation: missing SKU / missing name / duplicate SKU in file → row skipped and
  reported with row number. Re-import updates products by SKU (keeps uploaded photos). Max 25 MB / 50k rows.
- **Google Sheets / CSV URL:** a share link is converted to its CSV export URL; fetching is **SSRF-protected**
  (http/https only; private, loopback, link-local and reserved IPs refused; every redirect hop re-checked).
- **API:** create a catalog with products, or bulk upsert products into a catalog (≤5000 per call).
- **Store sync:** Shopify Admin GraphQL (`products(first:100, after:)` → title, handle, productType, vendor, tags,
  descriptionHtml, 3 images, first variant SKU/price, options) and WooCommerce REST v3 (`/products?per_page=100&page=`).
  Upsert by SKU into a chosen or new catalog, batches of 200. Scheduled sync every 6 h / daily / weekly.
- **Product photos:** upload JPG/PNG/WEBP/GIF ≤5 MB (≤10 per product) or add an image URL; served to the
  browser through an authenticated endpoint.

---

## 7. Review, versions & export

- Review page per product+language: editable fields (title with char counter, short, long with word counter,
  bullets one per line, meta title/description), SEO checklist, warnings, product data with editable keywords,
  photos, assignee, comments with @mentions, variant comparison ("Use this" = make current without copying),
  version history with Restore (creates a new version), Regenerate with engine choice and guidance.
- Saving an edit creates a **new version** (`source=manual`); history is linear and nothing is overwritten.
- **Approval:** one-step (reviewer approves) or two-step (reviewer → `in_review`, admin → `approved`; admins may
  approve drafts directly; reviewers cannot approve `in_review`). Bulk approve selected, or "approve all drafts with
  SEO ≥ N and no warnings". Rejection notifies the version author and the assignee.
- **Export** only approved versions (latest approved per product). Formats: generic CSV (all fields + HTML),
  Shopify import CSV, WooCommerce import CSV, Amazon / eBay / Daraz content CSVs, and direct push to Shopify
  (`productVariants(query:"sku:X")` → `productUpdate` with `descriptionHtml` and SEO title/description) or
  WooCommerce (`GET /products?sku=` → `PUT /products/{id}`). CSV files start with a UTF-8 BOM (Excel).

---

## 8. Security

- Passwords: bcrypt; min length 8. Login rate limit: 10 attempts / 15 min per email+IP (HTTP 429).
- Access token JWT claims: `sub`, `org`, `exp`, `typ=access`, `iat`, `ims` (issued-at ms), `sid` (session id).
  A token is rejected if its session is revoked or if it was issued before `password_changed_at`.
- Password reset: JWT (1 h) containing a fingerprint of the current password hash → single use. Responses never
  reveal whether an email exists. Reset/change revoke all sessions.
- **2FA (TOTP, RFC 6238):** 160-bit base32 secret, 6 digits, 30 s, ±1 step; QR code (SVG); 10 single-use recovery
  codes stored hashed. Login with 2FA returns `{mfa_required, mfa_token}` (5-minute JWT, typ=mfa) → `/auth/2fa/verify`.
  Workspace policy can require 2FA (members see a setup banner). SSO sign-ins don't ask for the app's TOTP.
- **SSO:** Google and Microsoft OIDC authorization-code flow with a signed, expiring `state`; userinfo endpoint for
  email (must be verified); existing email → sign in; unknown → new workspace (if `SSO_ALLOW_SIGNUP`); the token
  is returned in the URL **fragment** (`/sso-callback#token=`).
- **Audit log** for sign-ins/failures, password and 2FA changes, team changes, settings, keys, webhooks, store
  connections, imports, jobs, approvals, exports, plan changes, data export; filter + CSV export.
- **GDPR:** owner downloads a ZIP of JSON files (secrets and hashes excluded); owner deletes the workspace
  (type the name + password); members delete their own account (personal data anonymised, content kept).
- Encrypted at rest: store credentials, org Claude keys, TOTP secrets, webhook secrets. API shows secrets masked
  (`••••1234`). Webhook and URL-import targets must be public addresses.

---

## 9. Integrations for developers

- **API keys:** header `X-API-Key: cai_…` or `Authorization: Bearer cai_…`; roles admin/editor/reviewer; revocable;
  `last_used_at` tracked. OpenAPI docs at `/api/docs`.
- **Webhooks:** events `catalog.imported`, `job.completed`, `job.failed`, `description.approved`,
  `description.rejected`, `export.completed` (or `*`). JSON body `{event, created_at, data}`. Headers
  `X-Inkventory-Event`, `X-Inkventory-Delivery`, `X-Inkventory-Signature: sha256=<HMAC-SHA256(secret, raw body)>`.
  3 attempts (0, 5, 30 s). Delivery log, test ping, pause/resume. Webhook failures never break the triggering action.

---

## 10. Plans & billing

| Plan | Price | Descriptions/month | Team | AI |
|---|---|---|---|---|
| Free | $0 | 100 | 2 | free AI (Gemini/Groq/local) + Claude with own key |
| Pro | $29 | 2,000 | 5 | same + store push & sync |
| Business | $99 | 10,000 | 25 | same + priority support |

- Usage = AI-generated versions this calendar month; jobs that exceed the remaining allowance get HTTP 402.
- Online payment via **Lemon Squeezy**: checkout URL per plan with `checkout[custom][org_id]`; signed webhook
  (`X-Signature` HMAC) handles subscription created/updated/cancelled/expired → plan, status, renewal date.
- Manual payment: customer clicks "Request upgrade" (emails platform owners, shows "Requested" in Platform admin);
  the platform owner sets plan, status or a custom limit.

---

## 11. Reports, notifications & dashboard

- **Dashboard:** greeting, stat cards (products, waiting for review, approved, average SEO), recent jobs, usage
  this month, available AI engines, "Get started" steps for empty workspaces.
- **Reports** (7/30/90/365 days, optional catalog filter): totals, daily line chart (generated/approved/rejected),
  average SEO trend, SEO distribution (0–59/60–79/80–100), quality-flag counts, team productivity table, AI cost per
  catalog, status by language, daily CSV export. Charts are inline SVG with a hover tooltip and a legend.
- **Notifications:** bell with unread count (polled every 30 s), mark one/all read, click → linked page.

---

## 12. Frontend

**Routes:** `/login`, `/register`, `/forgot-password`, `/reset-password`, `/sso-callback`, `/` (dashboard),
`/catalogs`, `/catalogs/upload`, `/catalogs/:id` (tabs: Products & review, Generate, Export, Job history),
`/catalogs/:id/products/:pid?lang=`, `/reports`, `/presets` (brand voice & glossary), `/integrations`,
`/developer`, `/team`, `/billing`, `/settings`, `/security`, `/audit-log`, `/admin`.

**Generate tab:** engine dropdown grouped (Free cloud AI / Local / Claude & paid / Demo) with availability notes;
products (only missing / all / selected); language checkboxes; preset; variants 1–3; use photos; batch
instructions; summary with item count, products with photos, model, estimated cost ("Free"), estimated time,
plan remaining; helpful hints when 0 items or variants on free AI. Running job shows a progress card with Cancel.

**Products table:** filters (language, status incl. "awaiting final approval" and "flagged", assignee incl.
"assigned to me", search), checkbox selection → generate for selected / assign / approve / reject, bulk approve by
SEO threshold, pagination (25).

**Design system** (professional SaaS look, no gradients/emoji): Inter font; neutral slate surfaces
(`#f6f7f9` background, white cards, `#e4e7ec` borders, `#101828` text, `#667085` muted); one accent blue `#2563eb`;
status colors green/amber/red/blue with soft backgrounds; radius 8–12 px; 36 px controls; fixed 248 px white
sidebar with line icons and section labels (Setup, Workspace) and a workspace card; sticky topbar with breadcrumbs,
notification bell and avatar menu; status badges with a dot ("Draft", "In review", "Approved"); split-screen login
(form left, dark product panel right); responsive below 1000 px.

---

## 13. API reference (all paths prefixed with `/api`)

**Auth & account:** `POST auth/register` · `POST auth/login` (form: username, password) · `POST auth/2fa/verify` ·
`POST auth/forgot-password` · `POST auth/reset-password` · `POST auth/change-password` · `GET|DELETE auth/me` ·
`POST auth/2fa/setup|enable|disable|recovery-codes` · `GET auth/sessions` · `DELETE auth/sessions/{id}` ·
`POST auth/sessions/revoke-others` · `POST auth/logout` · `GET auth/sso/providers` ·
`GET auth/sso/{provider}/start|callback`
**Team:** `GET|POST users` · `PATCH users/{id}`
**Catalogs & products:** `POST catalogs/preview` · `POST catalogs` (multipart: file, name, mapping JSON, catalog_id?) ·
`POST catalogs/preview-url` · `POST catalogs/import-url` · `POST catalogs/api` · `POST catalogs/{id}/products` ·
`GET catalogs` · `GET|DELETE catalogs/{id}` · `GET catalogs/{id}/products?language&status&assignee&search&page&page_size` ·
`GET|PATCH products/{id}` · `POST products/{id}/images` · `POST products/{id}/image-urls` ·
`GET|DELETE products/{id}/images/{index}`
**Presets & glossary:** `GET|POST presets` · `PUT|DELETE presets/{id}` · `POST presets/learn-voice` ·
`GET|POST glossary` · `DELETE glossary/{id}`
**Generation:** `POST jobs/estimate` · `POST jobs` · `GET jobs?catalog_id` · `GET jobs/{id}` · `POST jobs/{id}/cancel`
**Descriptions & review:** `GET products/{id}/versions?language` · `GET descriptions/{id}` · `POST products/{id}/regenerate` ·
`PUT descriptions/{id}` (edit → new version) · `POST descriptions/{id}/make-current|restore|approve|reject` ·
`POST descriptions/bulk-review` · `POST catalogs/{id}/approve-drafts`
**Workflow:** `GET|POST products/{id}/comments` · `PATCH|DELETE comments/{id}` · `PATCH products/{id}/assign` ·
`POST catalogs/{id}/assign` · `GET notifications` · `GET notifications/unread-count` · `POST notifications/read`
**Stores & export:** `GET|POST integrations` · `PUT|DELETE integrations/{id}` · `POST integrations/{id}/test` ·
`POST integrations/{id}/sync` · `PATCH integrations/{id}/sync-settings` · `GET integrations/{id}/syncs` ·
`POST|GET exports` · `GET exports/{id}` · `GET exports/{id}/download`
**Developer:** `GET|POST api-keys` · `DELETE api-keys/{id}` · `GET webhooks/events` · `GET|POST webhooks` ·
`PATCH|DELETE webhooks/{id}` · `POST webhooks/{id}/test` · `GET webhooks/{id}/deliveries`
**Settings, audit, GDPR:** `GET|PATCH settings` · `POST settings/test-anthropic-key` · `GET settings/audit-log` ·
`GET settings/audit-log/actions` · `GET settings/audit-log/export` · `GET settings/export-data` ·
`DELETE settings/organization`
**Billing & platform:** `GET billing` · `POST billing/checkout` · `POST billing/request-upgrade` ·
`POST billing/webhook/lemonsqueezy` · `GET admin/organizations` · `PATCH admin/organizations/{id}`
**Misc:** `GET options` (languages, tones, lengths, channels, engines) · `GET dashboard` · `GET reports/summary` ·
`GET reports/daily.csv` · `GET health`

Error format: FastAPI default `{"detail": "..."}`; 401 unauthenticated, 402 plan/quota limit, 403 role, 404 not
found / other tenant, 409 conflict, 429 rate limit, 502 AI provider error.

### Configuration (environment variables)
`DATABASE_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `CORS_ORIGINS`, `APP_URL`,
`TASK_MODE`, `REDIS_URL`, `GENERATION_CONCURRENCY`, `STORAGE_BACKEND`, `STORAGE_DIR`, `S3_BUCKET`, `S3_REGION`,
`LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`, `ANTHROPIC_FALLBACKS`,
`CLAUDE_OWN_KEY_ONLY`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GROQ_API_KEY`,
`GROQ_MODEL`, `GEMINI_RPM`, `GROQ_RPM`, `FREE_CONCURRENCY`, `FREE_PATIENT_RETRIES`, `OLLAMA_BASE_URL`,
`OLLAMA_MODEL`, `OLLAMA_CONCURRENCY`, `LLM_MAX_RETRIES`, `EMAIL_BACKEND`, `EMAIL_FROM`, `SMTP_*`,
`SUPERADMIN_EMAILS`, `BILLING_CONTACT`, `LEMONSQUEEZY_*`, `GOOGLE_CLIENT_ID/SECRET`, `MICROSOFT_CLIENT_ID/SECRET/TENANT`,
`SSO_ALLOW_SIGNUP`, `LOGIN_ATTEMPTS_PER_15_MIN`, `MAX_UPLOAD_ROWS`, `DUPLICATE_SIMILARITY_THRESHOLD`.

---

## 14. Build plan with ready-to-use prompts

Build in this order and **run the tests after every phase**. Paste the whole specification above into the chat
first, then give one prompt at a time. Ask for complete files, not fragments.

1. **Foundation** — "Using the spec above, create the FastAPI backend skeleton: config (pydantic-settings), SQLAlchemy
   models for organizations, users, catalogs, products, style_presets, generation_jobs, description_versions,
   Alembic setup, JWT auth (register creates org + owner, login, me), role dependencies, tenant-scoped `get_owned`
   helper returning 404, and pytest fixtures with an isolated SQLite DB. Include tests."
2. **Import** — "Implement section 6 CSV import (parse, auto-mapping aliases, validation with row errors, preview
   and import endpoints, upsert by SKU) and the products list endpoint with filters and pagination. Include tests
   using a sample CSV with one missing-SKU and one duplicate row."
3. **AI pipeline** — "Implement section 5: prompt builder, provider interface, mock provider, Claude provider with
   structured outputs, OpenAI-compatible provider (Gemini/Groq) with JSON example instructions, robust JSON
   extraction, pacing, patient retries and daily-quota detection, SEO scoring (5.6), quality checks, versioning and
   the threaded job runner with cancel, variants and quota. Include tests with mocked HTTP transports."
4. **Review & export** — "Implement section 7: version history, edit/restore/make-current, one- and two-step
   approval, bulk approval, all CSV export formats and Shopify/WooCommerce push clients with tests using mocked HTTP."
5. **Brand & channels** — "Implement presets with channels (5.7), glossary (5.5 step 1), brand-voice learning (5.8)
   and product photos."
6. **Integrations** — "Implement section 6 store sync with scheduler, Google Sheets import with SSRF protection,
   API keys and signed webhooks (section 9)."
7. **Security** — "Implement section 8: sessions, password reset/change, rate limiting, TOTP 2FA with QR and
   recovery codes, Google/Microsoft SSO, audit log, GDPR export/deletion, encryption of secrets."
8. **SaaS** — "Implement section 10 plans, quota, Lemon Squeezy checkout + webhook, upgrade requests, platform admin;
   section 11 notifications, comments, assignment and reports."
9. **Frontend** — "Build the React + TypeScript + Vite frontend for section 12 with the described design system:
   typed API client, auth pages, app shell (sidebar, topbar, breadcrumbs, bell, user menu), then each page."
10. **Deployment** — "Write Dockerfiles, docker-compose (postgres, redis, api, worker with beat, nginx web),
    .env.example, GitHub Actions CI and a README."

**Acceptance checklist:** a new user can sign up, upload the sample CSV (8 imported, 2 rows reported), generate
English + Urdu descriptions with the mock engine, edit one (version 2), approve all, export a Shopify CSV, see the
job in Reports and the actions in the Audit log; a second organization sees none of that data.
