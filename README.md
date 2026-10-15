# Inkventory: AI Product Description Generator for Catalogs

Inkventory is a multi-tenant SaaS tool that writes unique, SEO-optimized product descriptions in bulk for large e-commerce catalogs. Each business that signs up gets its own workspace.

**Flow:** upload a CSV → AI writes descriptions (any tone, 15 languages) → SEO score and quality checks → team reviews and approves → export to CSV, Shopify or WooCommerce.

## Features

| Requirement | Implementation |
|---|---|
| Bulk catalog upload (CSV/API) | CSV upload with automatic column detection, a mapping screen and row validation (missing or duplicate SKUs are reported). REST endpoints for pushing products programmatically. Re-uploading updates products by SKU. |
| AI-generated descriptions | Claude (default), OpenAI, or an offline demo provider. Each product gets a title, short description, long description, bullet points, meta title and meta description, returned as schema-validated JSON. Products run in parallel inside background jobs with live progress, cancel, and per-product error reporting. |
| Tone & style customization | 5 built-in presets plus custom presets: tone, length, brand guidelines and up to 3 example descriptions in the brand voice. Extra instructions can be added per batch. |
| SEO keyword optimization | Per-product keywords (the first one is primary). A rule-based SEO score (0–100) covers keyword placement, coverage, density, title and meta lengths, description length and readability. Every check is shown to the reviewer. |
| Multi-language generation | 15 languages (English, Urdu, Arabic, Hindi, Spanish, French, German, …), each written natively rather than translated. RTL text displays correctly. |
| Review & approval workflow | Draft → approved / rejected, with review notes. Supports inline editing, bulk approve/reject of selected rows, and "approve all drafts with SEO ≥ N and no warnings". |
| Direct export to e-commerce platforms | Generic CSV, Shopify-import CSV, WooCommerce-import CSV, and a direct push to Shopify (Admin GraphQL) or WooCommerce (REST v3), matched by SKU. |
| Version history & regeneration | Nothing is overwritten. Every AI generation, manual edit or restore creates a new version, and any version can be restored. Products can be regenerated with extra guidance. |

**Beyond the original spec:**

- **Hallucination guard.** The prompt forbids invented facts. Numbers in the output that don't appear in the product data are also flagged for review.
- **Duplicate-content detection.** Descriptions that are near-identical to others in the same catalog are flagged.
- **Prompt-injection safety.** Product data is passed as delimited data, and the prompt tells the model to ignore any instructions inside it.
- **Cost control.** Each job shows an estimated cost before it starts and tracks real token usage and cost. Organizations have a monthly generation quota. Prompt caching cuts repeated-prompt cost.
- **Teams and roles.** Owner, admin, editor and reviewer roles. Every tenant's data is isolated by `org_id`.
- **Dashboard.** Usage, spend, review backlog, average SEO score and recent jobs.

## Enterprise features

**AI**
- **Product photos.** Photos come from an image column in the CSV, uploads or URLs. Vision-capable engines (Claude, OpenAI, Ollama vision models) use them for visible details only.
- **Variants.** 1–3 variants per product. The highest-scoring one is selected automatically, and reviewers can compare them side by side and switch.
- **Brand voice learning.** The AI analyses 3–20 existing descriptions (pasted, or taken from a catalog) and saves a style-guide preset.
- **Sales channels.** Amazon, eBay, Daraz and Shopify profiles add listing rules (title length, bullets, banned phrases), compliance checks and marketplace CSV exports.
- **Glossary.** Words to avoid (optionally auto-replaced) and terms to keep exactly, applied to every generation.

**Integrations**
- **Store import and sync.** Products are imported from Shopify (GraphQL) or WooCommerce (REST), matched by SKU. Syncs can be scheduled (every 6 h / daily / weekly) via Celery beat in production, or a scheduler thread in local mode.
- **Google Sheets / CSV-URL import.** Includes SSRF protection: private and internal addresses are blocked, and every redirect hop is checked.
- **API keys.** Each key has a role, is stored as a hash and is shown once. Send it as `X-API-Key` or a Bearer token. Account actions (passwords, keys, billing) refuse API keys. Interactive docs are at `/api/docs`.
- **Webhooks.** Events: `catalog.imported`, `job.completed`, `job.failed`, `description.approved`, `description.rejected` and `export.completed`. Each request is HMAC-SHA256 signed (`X-Inkventory-Signature`), failures are retried 3 times, and a delivery log, test pings and pause/resume are available.

**Security**
- **Two-step login (TOTP).** Uses an authenticator app with a QR code, plus 10 single-use recovery codes. A workspace can require it for everyone.
- **Sign in with Google / Microsoft (OpenID Connect).** CSRF-safe `state`. The token is passed back in the URL fragment.
- **Sessions per device.** Users can see and sign out devices. Changing or resetting a password signs out everywhere, and deactivating a user signs them out too.
- **Audit log.** Records sign-ins (and failures), settings, keys, webhooks, imports, jobs, approvals, exports and plan changes, with IP and a CSV export.
- **GDPR.** Owners can download all workspace data as a ZIP (secrets excluded) and delete the workspace. Members can delete their own account (personal data anonymised).

**Team workflow & reporting**
- **Two-step approval.** A reviewer approves first and an admin gives final approval. Only final-approved content is exported.
- **Comments and @mentions** on each product, which can be resolved. Products can be assigned to people (singly or in bulk), with an "Assigned to me" filter.
- **In-app notifications (the bell):** assignments, mentions, approvals needed, rejections and finished jobs.
- **Reports.** Daily activity, SEO trend, SEO distribution, quality flags, team productivity, AI cost per catalog and status by language, with a CSV export.

## Architecture

```
React (Vite) ──/api──► FastAPI ──► PostgreSQL (products, versions, jobs)
                          │  └──► S3 / local disk (uploaded & exported files)
                          ▼
                     Redis queue ──► Celery worker ──► Claude API (thread pool, per-product)
                                          └──► SEO scoring + quality checks ──► new DescriptionVersion
```

```
backend/
  app/
    main.py              FastAPI app, startup (tables, built-in presets)
    models.py            SQLAlchemy models (multi-tenant)
    schemas.py           Pydantic request/response models
    routers/             auth+team, catalogs, presets, jobs, descriptions, exports, dashboard
    services/
      csv_import.py      parsing, column auto-mapping, validation
      prompts.py         system/user prompt construction, languages, tones
      llm/               provider interface + Anthropic / OpenAI / mock providers
      generation.py      job runner, versioning, cost tracking
      seo.py, quality.py SEO score, hallucination & duplicate checks
      exporters/         CSV formats, Shopify & WooCommerce clients
    tasks.py             job dispatch: celery | thread | sync
  tests/                 20 tests (API flows, services, Claude provider with mocked HTTP)
frontend/src/            React dashboard (pages/, api.ts, components.tsx)
sample_data/products.csv sample catalog (includes 2 intentionally bad rows)
docker-compose.yml       Postgres + Redis + API + worker + web
```

## Run locally (no Docker)

Requires Python 3.11+ and Node 20+.

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then set ANTHROPIC_API_KEY (or LLM_PROVIDER=mock for a demo)
uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

Open http://localhost:5173, create an account, then upload `sample_data/products.csv`.

Local mode uses SQLite and runs jobs in a background thread, so you don't need Redis or Postgres. API docs are at http://localhost:8000/docs.

## AI engines: free for everyone, Claude with your own key

Users pick an engine per job (**Generate → AI engine**) and per regeneration.

| Engine | Who can use it | Cost | Notes |
|---|---|---|---|
| **Google Gemini** (`GEMINI_API_KEY`) | every workspace, every plan | free tier (platform key) | fast, good in Urdu/Arabic, reads photos; daily and per-minute limits |
| **Groq** (`GROQ_API_KEY`) | every workspace, every plan | free tier (platform key) | very fast, best for English; text only |
| **Local Ollama** | every workspace | free | runs on the server; slow without a GPU |
| **Claude** | workspaces that saved **their own** Claude key in Settings | billed by Anthropic to that workspace | best quality in every language; the platform's own Claude key is not shared (`CLAUDE_OWN_KEY_ONLY=true`) |
| **Demo** | everyone | free | placeholder text |

`LLM_PROVIDER=auto` makes the default the first configured free engine (Gemini, then Groq, then Ollama, then demo). Free tiers allow only a few requests per minute, so free cloud jobs run 2 at a time (`FREE_CONCURRENCY`). Free tiers may use submitted data to improve the provider's models, so before going live with customer data, move to a paid tier or have customers use Claude with their own key.
## Run with Docker (production-like)

```bash
copy backend\.env.example backend\.env   # set ANTHROPIC_API_KEY and SECRET_KEY
docker compose up --build
```

Open http://localhost:8080. This stack runs PostgreSQL, Redis, the API, a Celery worker and nginx serving the UI.

For AWS, run the same containers on ECS (or a single EC2 host with Compose). Use RDS for PostgreSQL and ElastiCache for Redis, and set `STORAGE_BACKEND=s3` with `S3_BUCKET`. The GitHub Actions CI (`.github/workflows/ci.yml`) runs the tests, builds the frontend and builds the images.

## Configuration

All settings are environment variables (see `backend/.env.example`). The important ones:

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `mock` | default engine: `ollama` (free, local), `anthropic`, `openai`, or `mock` (demo text) |
| `OLLAMA_MODEL` | `qwen2.5:7b` | any model installed in Ollama (all installed models appear in the app) |
| `ANTHROPIC_API_KEY` | – | required for `anthropic` |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | e.g. `claude-sonnet-5-5` or `claude-haiku-4-5` for lower cost |
| `ANTHROPIC_EFFORT` | `medium` | `low` is cheaper and faster; `high` is more thorough |
| `TASK_MODE` | `thread` | `celery` in production |
| `DATABASE_URL` | SQLite file | `postgresql+psycopg://…` |
| `SECRET_KEY` | dev value | **must** be changed in production (32+ random chars) |
| `ENCRYPTION_KEY` | derived from `SECRET_KEY` | Fernet key for stored secrets; never change it once secrets exist |
| `APP_URL` | `http://localhost:5173` | public URL, used in email links |
| `SUPERADMIN_EMAILS` | – | platform owner emails; they get the **Platform admin** panel |
| `EMAIL_BACKEND` | `console` | `smtp` in production (set `SMTP_*`, `EMAIL_FROM`) |
| `LEMONSQUEEZY_*` | – | optional online payments (checkout links, variant ids, webhook secret) |
| `BILLING_CONTACT` | – | shown to customers who want to pay by bank transfer / invoice |
| `GOOGLE_CLIENT_ID` / `_SECRET` | – | enables "Continue with Google"; redirect URI `{APP_URL}/api/auth/sso/google/callback` |
| `MICROSOFT_CLIENT_ID` / `_SECRET` / `_TENANT` | – / – / `common` | enables "Continue with Microsoft"; set the tenant id to allow only your company |
| `SSO_ALLOW_SIGNUP` | `true` | new SSO emails get their own workspace; `false` = invite-only |
| `OLLAMA_CONCURRENCY` | `1` | parallel requests to a local model |

## Selling it: plans, billing and accounts

- **Plans** (`backend/app/plans.py`): Free (100 descriptions/month, 2 users, local AI only), Pro (2,000, 5 users, Claude) and Business (10,000, 25 users). Edit prices, limits and features in that file.
- **Each company's own Claude key** (Settings): optional. When it's saved, that company can use Claude on any plan and is billed by Anthropic directly. Keys are encrypted at rest (Fernet) and only ever shown masked.
- **Online payments**: create Pro/Business subscription products in Lemon Squeezy and put their checkout links and variant ids in `.env`. Point the webhook at `/api/billing/webhook/lemonsqueezy`. Upgrades, renewals, cancellations and expiries update the plan automatically (the signature is verified).
- **Bank transfer / invoice**: in **Platform admin**, the owner sets any organization's plan, status or a custom monthly limit.
- **Accounts**: password reset by email (single-use links, valid for 1 hour), password change (signs out other sessions), welcome, team-invite and job-finished emails, and login rate limiting (10 attempts per 15 minutes per email + IP).
- **Database migrations**: Alembic (`backend/migrations`) runs automatically on startup. Databases created before migrations existed are detected and upgraded in place. To create a new migration after changing models: `alembic revision --autogenerate -m "..."`.

## Roles

| Role | Can do |
|---|---|
| Owner / Admin | everything, plus team management and store connections |
| Editor | upload catalogs, generate, edit, regenerate, export |
| Reviewer | approve or reject descriptions |

## Tests

```bash
cd backend && pytest -q          # 69 tests, no API key needed
cd frontend && npm run build     # type-check + production build
```

## Known limitations / next steps

- Server setup (domain, HTTPS, backups, monitoring) is deliberately not part of the code; see the deployment notes above.
- The Lemon Squeezy integration is tested against signed sample webhooks, but not yet against a live Lemon Squeezy store. Run a test-mode purchase before launch.
- Login rate limiting is kept in memory per API process. Move it to Redis when running several API servers.
- A `past_due` or `cancelled` status is shown to the customer but doesn't block usage on its own. Decide your grace-period policy.
- Terms of Service and a Privacy Policy still need to be written for your business.
- Duplicate detection compares against the 500 most recent descriptions per catalog/language. Very large catalogs would benefit from MinHash/LSH.
- For catalogs of 50k+ products, switch the job runner to the Message Batches API (about 50% cheaper, asynchronous).
- Google/Microsoft sign-in, the Shopify/WooCommerce import and webhooks are tested against simulated provider responses. Try each with real credentials before launch.
- The marketplace CSV exports (Amazon, eBay, Daraz) contain the content columns only. Merge them into each marketplace's category template, which has many other required columns (price, stock and so on).
- In local "thread" mode, the store-sync scheduler runs inside every API process. Use Celery beat (Docker setup) when running more than one API process.
