// Typed API client for the FastAPI backend.

export type Role = "owner" | "admin" | "editor" | "reviewer";

export interface Me {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  is_superadmin: boolean;
  totp_enabled: boolean;
  must_setup_2fa: boolean;
  organization: { id: number; name: string; monthly_generation_limit: number; plan: string; plan_status: string };
}

export interface OrgSettings {
  name: string;
  has_own_anthropic_key: boolean;
  anthropic_key_hint: string;
  notify_on_job_complete: boolean;
  require_2fa: boolean;
  approval_steps: number;
}

export interface LoginResult {
  access_token: string;
  mfa_required: boolean;
  mfa_token: string | null;
}

export interface SessionInfo {
  id: string;
  method: string;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  current: boolean;
}

export interface AuditEntry {
  id: number;
  user_id: number | null;
  actor: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  ip: string | null;
  created_at: string;
}

export interface Plan {
  id: string;
  name: string;
  price_label: string;
  monthly_generations: number;
  max_users: number;
  engine_kinds: string[];
  features: string[];
  can_checkout: boolean;
}

export interface Billing {
  plan: Plan;
  plan_status: string;
  plan_renews_at: string | null;
  billing_provider: string | null;
  usage_this_month: number;
  monthly_limit: number;
  team_members: number;
  plans: Plan[];
  billing_contact: string;
  requested_plan: string | null;
  requested_at: string | null;
}

export interface AdminOrg {
  id: number;
  name: string;
  owner_email: string | null;
  plan: string;
  plan_status: string;
  monthly_generation_limit: number;
  usage_this_month: number;
  products: number;
  members: number;
  has_own_anthropic_key: boolean;
  billing_provider: string | null;
  requested_plan: string | null;
  requested_at: string | null;
  created_at: string;
}

export interface UserRow {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface Catalog {
  id: number;
  name: string;
  source: string;
  source_filename: string | null;
  product_count: number;
  created_at: string;
}

export interface CatalogCard extends Catalog {
  thumbnails: { product_id: number; index: number; url: string | null }[];
  generated: number;
  approved: number;
  pending_review: number;
  last_job_status: Job["status"] | null;
}

export interface CatalogDetail extends Catalog {
  column_mapping: Record<string, string>;
  stats: {
    total_products: number;
    by_language: Record<string, { draft: number; in_review?: number; approved: number; rejected: number }>;
    approval_steps: number;
    avg_seo_score: number | null;
  };
}

export interface Warning {
  type: string;
  message: string;
}

export interface VersionSummary {
  id: number;
  language: string;
  version_no: number;
  status: string;
  seo_score: number;
  title: string;
  warnings: Warning[];
}

export interface Product {
  id: number;
  catalog_id: number;
  sku: string;
  name: string;
  category: string | null;
  brand: string | null;
  price: string | null;
  attributes: Record<string, string>;
  keywords: string[];
  images: string[];
  original_description: string | null;
  assignee_id: number | null;
  image_analysis?: PhotoAnalysis | null;
  photo_analysis_current?: boolean;
}

export interface PhotoAnalysis {
  product_type: string;
  colors: string[];
  visible_features: string[];
  style: string;
  looks_like_materials: string[];
  summary: string;
  mismatch: string;
  photos: number;
  model: string;
  analyzed_at: string;
}

export interface Comment {
  id: number;
  product_id: number;
  language: string | null;
  version_id: number | null;
  user_id: number | null;
  author: string;
  body: string;
  mentions: number[];
  resolved: boolean;
  created_at: string;
}

export interface AppNotification {
  id: number;
  kind: string;
  title: string;
  body: string;
  link: string | null;
  read_at: string | null;
  created_at: string;
}

export interface Report {
  days: number;
  since: string;
  totals: { generated: number; edited: number; approved: number; rejected: number; avg_seo: number | null; cost_usd: number };
  series: { date: string; generated: number; edited: number; approved: number; rejected: number; avg_seo: number | null }[];
  team: { user_id: number; name: string; email: string; generated: number; edited: number; approved: number; rejected: number; comments: number }[];
  cost_by_catalog: { catalog_id: number; catalog: string; cost_usd: number; generated: number }[];
  languages: Record<string, { draft: number; in_review: number; approved: number; rejected: number }>;
  warnings: Record<string, number>;
  seo_distribution: Record<string, number>;
}

export interface ProductRow extends Product {
  current: Record<string, VersionSummary>;
  catalog_name?: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface SeoCheck {
  id: string;
  label: string;
  passed: boolean;
  weight: number;
  detail: string;
}

export interface Version {
  id: number;
  product_id: number;
  job_id: number | null;
  language: string;
  version_no: number;
  is_current: boolean;
  source: string;
  title: string;
  short_description: string;
  long_description: string;
  bullet_points: string[];
  meta_title: string;
  meta_description: string;
  seo_score: number;
  seo_report: { checks?: SeoCheck[] };
  warnings: Warning[];
  status: "draft" | "in_review" | "approved" | "rejected";
  review_note: string | null;
  reviewed_at: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface Preset {
  id: number;
  name: string;
  tone: string;
  length: "short" | "medium" | "long";
  channel: string;
  instructions: string;
  examples: string[];
  is_builtin: boolean;
}

export interface Job {
  id: number;
  catalog_id: number;
  preset_id: number | null;
  engine: string | null;
  variants: number;
  use_images: boolean;
  languages: string[];
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  total: number;
  completed: number;
  failed: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  errors: { sku?: string; language?: string; error: string }[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Integration {
  id: number;
  platform: "shopify" | "woocommerce";
  name: string;
  config: Record<string, string>;
  created_at: string;
  sync_catalog_id: number | null;
  auto_sync_hours: number | null;
  last_synced_at: string | null;
}

export interface SyncRun {
  id: number;
  integration_id: number;
  catalog_id: number | null;
  trigger: string;
  status: string;
  imported: number;
  updated: number;
  errors: { sku?: string; error: string }[];
  created_at: string;
  finished_at: string | null;
}

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  role: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface Webhook {
  id: number;
  url: string;
  events: string[];
  is_active: boolean;
  created_at: string;
}

export interface WebhookDelivery {
  id: number;
  event: string;
  attempts: number;
  status_code: number | null;
  success: boolean;
  error: string | null;
  created_at: string;
  delivered_at: string | null;
}

type Preview = {
  headers: string[]; sample_rows: Record<string, string>[]; row_count: number; suggested_mapping: Record<string, string>;
  product_count?: number | null; detected_format?: string | null;
};
type ImportResult = { catalog: Catalog; imported: number; updated: number; errors: { row: number; sku?: string; error: string }[] };

export interface ExportJob {
  id: number;
  catalog_id: number;
  integration_id: number | null;
  format: string;
  language: string;
  status: string;
  total: number;
  succeeded: number;
  failed: number;
  errors: { sku?: string; error: string }[];
  file_key: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Engine {
  id: string;
  label: string;
  kind: "free" | "cloud" | "local" | "demo";
  free: boolean;
  available: boolean;
  note: string;
  needs_upgrade: boolean;
}

export interface Options {
  languages: Record<string, string>;
  tones: Record<string, string>;
  lengths: Record<string, [number, number]>;
  llm_provider: string;
  llm_model: string;
  default_engine: string;
  engines: Engine[];
  channels: Record<string, string>;
}

export interface GlossaryTerm {
  id: number;
  term: string;
  kind: "avoid" | "keep";
  replacement: string | null;
}

export interface Dashboard {
  catalogs: number;
  products: number;
  descriptions: { draft: number; in_review?: number; approved: number; rejected: number; flagged: number };
  avg_seo_score: number | null;
  usage: { this_month: number; limit: number };
  total_cost_usd: number;
  total_generated: number;
  recent_jobs: Job[];
  quality: { total: number; seo_buckets: { low: number; medium: number; high: number }; flags: Record<string, number>; clean_share: number | null };
  attention: { product_id: number; catalog_id: number; name: string; sku: string; language: string; seo_score: number; flags: string[]; message: string | null }[];
  trend: { date: string; generated: number; approved: number }[];
}

const TOKEN_KEY = "catalogai_token";

export const auth = {
  get token(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string | null) {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable */
    }
  },
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

function errorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: { loc?: string[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: string }).message);
  return "Request failed";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (auth.token) headers.set("Authorization", `Bearer ${auth.token}`);
  if (init.body && !(init.body instanceof FormData) && !(init.body instanceof URLSearchParams)) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`/api${path}`, { ...init, headers });
  if (res.status === 401 && auth.token) {
    auth.set(null);
    window.location.href = "/login";
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, errorMessage(detail), detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  // auth
  register: (body: { organization_name: string; full_name: string; email: string; password: string }) =>
    request<{ access_token: string }>("/auth/register", { method: "POST", body: json(body) }),
  login: (email: string, password: string) =>
    request<LoginResult>("/auth/login", {
      method: "POST",
      body: new URLSearchParams({ username: email, password }),
    }),
  me: () => request<Me>("/auth/me"),
  verifyMfa: (mfa_token: string, code: string) =>
    request<{ access_token: string }>("/auth/2fa/verify", { method: "POST", body: json({ mfa_token, code }) }),
  ssoProviders: () => request<{ id: string; name: string }[]>("/auth/sso/providers"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  setup2fa: () => request<{ secret: string; otpauth_uri: string; qr_svg: string }>("/auth/2fa/setup", { method: "POST" }),
  enable2fa: (code: string) => request<{ recovery_codes: string[] }>("/auth/2fa/enable", { method: "POST", body: json({ code }) }),
  disable2fa: (password: string, code: string) =>
    request<void>("/auth/2fa/disable", { method: "POST", body: json({ password, code }) }),
  newRecoveryCodes: (code: string) =>
    request<{ recovery_codes: string[] }>("/auth/2fa/recovery-codes", { method: "POST", body: json({ code }) }),
  sessions: () => request<SessionInfo[]>("/auth/sessions"),
  revokeSession: (id: string) => request<void>(`/auth/sessions/${id}`, { method: "DELETE" }),
  revokeOtherSessions: () => request<{ revoked: number }>("/auth/sessions/revoke-others", { method: "POST" }),
  deleteMyAccount: (password: string) => request<void>("/auth/me", { method: "DELETE", body: json({ password }) }),
  auditLog: (params: { action?: string; page?: number }) => {
    const qs = new URLSearchParams();
    if (params.action) qs.set("action", params.action);
    qs.set("page", String(params.page ?? 1));
    return request<AuditEntry[]>(`/settings/audit-log?${qs}`);
  },
  auditActions: () => request<Record<string, string>>("/settings/audit-log/actions"),
  downloadFile: async (path: string, fallbackName: string) => {
    const res = await fetch(`/api${path}`, { headers: { Authorization: `Bearer ${auth.token}` } });
    if (!res.ok) throw new ApiError(res.status, "Download failed");
    const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  },
  deleteOrganization: (organization_name: string, password: string) =>
    request<void>("/settings/organization", { method: "DELETE", body: json({ organization_name, password }) }),
  forgotPassword: (email: string) =>
    request<{ message: string }>("/auth/forgot-password", { method: "POST", body: json({ email }) }),
  resetPassword: (token: string, new_password: string) =>
    request<LoginResult>("/auth/reset-password", { method: "POST", body: json({ token, new_password }) }),
  changePassword: (current_password: string, new_password: string) =>
    request<{ access_token: string }>("/auth/change-password", {
      method: "POST",
      body: json({ current_password, new_password }),
    }),

  // organization settings
  orgSettings: () => request<OrgSettings>("/settings"),
  updateOrgSettings: (body: Partial<{ name: string; anthropic_api_key: string; notify_on_job_complete: boolean; require_2fa: boolean; approval_steps: number }>) =>
    request<OrgSettings>("/settings", { method: "PATCH", body: json(body) }),
  testOwnKey: () => request<{ ok: boolean; detail: string }>("/settings/test-anthropic-key", { method: "POST" }),

  // billing
  billing: () => request<Billing>("/billing"),
  checkout: (plan: string) => request<{ url: string }>("/billing/checkout", { method: "POST", body: json({ plan }) }),
  requestUpgrade: (plan: string, message = "") =>
    request<{ message: string }>("/billing/request-upgrade", { method: "POST", body: json({ plan, message }) }),

  // platform admin
  adminOrgs: () => request<AdminOrg[]>("/admin/organizations"),
  adminUpdateOrg: (id: number, body: Partial<{ plan: string; plan_status: string; monthly_generation_limit: number }>) =>
    request<AdminOrg>(`/admin/organizations/${id}`, { method: "PATCH", body: json(body) }),

  // team
  users: () => request<UserRow[]>("/users"),
  createUser: (body: { email: string; full_name: string; password: string; role: string }) =>
    request<UserRow>("/users", { method: "POST", body: json(body) }),
  updateUser: (id: number, body: Partial<{ role: string; is_active: boolean; full_name: string }>) =>
    request<UserRow>(`/users/${id}`, { method: "PATCH", body: json(body) }),

  // meta
  options: () => request<Options>("/options"),
  dashboard: () => request<Dashboard>("/dashboard"),

  // catalogs
  catalogs: () => request<CatalogCard[]>("/catalogs"),
  catalog: (id: number) => request<CatalogDetail>(`/catalogs/${id}`),
  deleteCatalog: (id: number) => request<void>(`/catalogs/${id}`, { method: "DELETE" }),
  previewCatalog: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{
      headers: string[];
      sample_rows: Record<string, string>[];
      row_count: number;
      suggested_mapping: Record<string, string>;
      product_count?: number | null;
      detected_format?: string | null;
    }>("/catalogs/preview", { method: "POST", body: form });
  },
  uploadCatalog: (file: File, name: string, mapping: Record<string, string>, catalogId?: number) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    form.append("mapping", JSON.stringify(mapping));
    if (catalogId) form.append("catalog_id", String(catalogId));
    return request<{ catalog: Catalog; imported: number; updated: number; errors: { row: number; sku?: string; error: string }[] }>(
      "/catalogs",
      { method: "POST", body: form },
    );
  },
  products: (catalogId: number, params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && qs.set(k, String(v)));
    return request<Page<ProductRow>>(`/catalogs/${catalogId}/products?${qs}`);
  },
  allProducts: (params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && qs.set(k, String(v)));
    return request<Page<ProductRow>>(`/products?${qs}`);
  },
  product: (id: number) => request<Product>(`/products/${id}`),
  updateProduct: (id: number, body: Partial<Pick<Product, "name" | "category" | "brand" | "keywords" | "attributes">>) =>
    request<Product>(`/products/${id}`, { method: "PATCH", body: json(body) }),

  // presets
  presets: () => request<Preset[]>("/presets"),
  createPreset: (body: Omit<Preset, "id" | "is_builtin">) =>
    request<Preset>("/presets", { method: "POST", body: json(body) }),
  updatePreset: (id: number, body: Omit<Preset, "id" | "is_builtin">) =>
    request<Preset>(`/presets/${id}`, { method: "PUT", body: json(body) }),
  deletePreset: (id: number) => request<void>(`/presets/${id}`, { method: "DELETE" }),

  // generation
  estimateJob: (body: JobInput) =>
    request<{
      items: number;
      model: string;
      estimated_cost_usd: number;
      estimated_minutes: number;
      remaining_quota: number;
      products_with_images: number;
    }>("/jobs/estimate", {
      method: "POST",
      body: json(body),
    }),
  createJob: (body: JobInput) => request<Job>("/jobs", { method: "POST", body: json(body) }),
  jobs: (catalogId?: number) => request<Job[]>(`/jobs${catalogId ? `?catalog_id=${catalogId}` : ""}`),
  job: (id: number) => request<Job>(`/jobs/${id}`),
  cancelJob: (id: number) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),

  // review
  versions: (productId: number, language?: string) =>
    request<Version[]>(`/products/${productId}/versions${language ? `?language=${language}` : ""}`),
  regenerate: (
    productId: number,
    body: { language: string; preset_id?: number | null; engine?: string | null; extra_instructions?: string; use_images?: boolean },
  ) =>
    request<Version>(`/products/${productId}/regenerate`, { method: "POST", body: json(body) }),
  editVersion: (id: number, body: Pick<Version, "title" | "short_description" | "long_description" | "bullet_points" | "meta_title" | "meta_description">) =>
    request<Version>(`/descriptions/${id}`, { method: "PUT", body: json(body) }),
  restoreVersion: (id: number) => request<Version>(`/descriptions/${id}/restore`, { method: "POST" }),
  makeCurrent: (id: number) => request<Version>(`/descriptions/${id}/make-current`, { method: "POST" }),

  // workflow
  comments: (productId: number, language?: string) =>
    request<Comment[]>(`/products/${productId}/comments${language ? `?language=${language}` : ""}`),
  addComment: (productId: number, body: { body: string; language?: string; version_id?: number | null; mentions: number[] }) =>
    request<Comment>(`/products/${productId}/comments`, { method: "POST", body: json(body) }),
  resolveComment: (id: number, resolved: boolean) => request<Comment>(`/comments/${id}?resolved=${resolved}`, { method: "PATCH" }),
  deleteComment: (id: number) => request<void>(`/comments/${id}`, { method: "DELETE" }),
  assignProduct: (productId: number, user_id: number | null) =>
    request<Product>(`/products/${productId}/assign`, { method: "PATCH", body: json({ user_id }) }),
  bulkAssign: (catalogId: number, product_ids: number[], user_id: number | null) =>
    request<{ updated: number }>(`/catalogs/${catalogId}/assign`, { method: "POST", body: json({ product_ids, user_id }) }),
  notifications: () => request<AppNotification[]>("/notifications"),
  unreadCount: () => request<{ count: number }>("/notifications/unread-count"),
  markRead: (ids?: number[]) => request<{ updated: number }>("/notifications/read", { method: "POST", body: json({ ids: ids ?? null }) }),
  report: (days: number, catalogId?: number | null) =>
    request<Report>(`/reports/summary?days=${days}${catalogId ? `&catalog_id=${catalogId}` : ""}`),

  // product photos
  uploadImage: (productId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Product>(`/products/${productId}/images`, { method: "POST", body: form });
  },
  addImageUrl: (productId: number, url: string) =>
    request<Product>(`/products/${productId}/image-urls`, { method: "POST", body: json({ url }) }),
  analyzePhotos: (productId: number, engine?: string | null) =>
    request<Product>(`/products/${productId}/analyze-photos`, { method: "POST", body: json({ engine: engine ?? null }) }),
  deleteImage: (productId: number, index: number) =>
    request<Product>(`/products/${productId}/images/${index}`, { method: "DELETE" }),
  imageObjectUrl: async (productId: number, index: number) => {
    const res = await fetch(`/api/products/${productId}/images/${index}`, { headers: { Authorization: `Bearer ${auth.token}` } });
    if (!res.ok) throw new ApiError(res.status, "Image not found");
    return URL.createObjectURL(await res.blob());
  },

  // brand voice & glossary
  learnVoice: (body: { name: string; samples: string[]; catalog_id?: number | null; engine?: string | null }) =>
    request<{ preset: Preset; summary: string; samples_used: number }>("/presets/learn-voice", {
      method: "POST",
      body: json(body),
    }),
  glossary: () => request<GlossaryTerm[]>("/glossary"),
  addGlossaryTerm: (body: { term: string; kind: string; replacement?: string | null }) =>
    request<GlossaryTerm>("/glossary", { method: "POST", body: json(body) }),
  deleteGlossaryTerm: (id: number) => request<void>(`/glossary/${id}`, { method: "DELETE" }),
  approve: (id: number, note?: string) =>
    request<Version>(`/descriptions/${id}/approve`, { method: "POST", body: json({ note }) }),
  reject: (id: number, note?: string) =>
    request<Version>(`/descriptions/${id}/reject`, { method: "POST", body: json({ note }) }),
  bulkReview: (version_ids: number[], action: "approve" | "reject") =>
    request<{ updated: number; skipped: number; awaiting_final_approval: number }>("/descriptions/bulk-review", { method: "POST", body: json({ version_ids, action }) }),
  approveDrafts: (catalogId: number, body: { language: string; min_seo_score: number; skip_flagged: boolean }) =>
    request<{ updated: number; skipped: number; awaiting_final_approval: number }>(`/catalogs/${catalogId}/approve-drafts`, { method: "POST", body: json(body) }),

  // integrations & exports
  integrations: () => request<Integration[]>("/integrations"),
  createIntegration: (body: { platform: string; name: string; config: Record<string, string> }) =>
    request<Integration>("/integrations", { method: "POST", body: json(body) }),
  deleteIntegration: (id: number) => request<void>(`/integrations/${id}`, { method: "DELETE" }),
  testIntegration: (id: number) => request<{ ok: boolean; detail: string }>(`/integrations/${id}/test`, { method: "POST" }),
  startSync: (id: number, catalog_id?: number | null) =>
    request<SyncRun>(`/integrations/${id}/sync`, { method: "POST", body: json({ catalog_id: catalog_id ?? null }) }),
  syncSettings: (id: number, body: { auto_sync_hours: number | null; sync_catalog_id?: number | null }) =>
    request<Integration>(`/integrations/${id}/sync-settings`, { method: "PATCH", body: json(body) }),
  syncRuns: (id: number) => request<SyncRun[]>(`/integrations/${id}/syncs`),

  // URL / Google Sheets import
  previewUrl: (url: string) => request<Preview>("/catalogs/preview-url", { method: "POST", body: json({ url }) }),
  importUrl: (url: string, name: string, mapping: Record<string, string>) =>
    request<ImportResult>("/catalogs/import-url", { method: "POST", body: json({ url, name, mapping }) }),

  // developer
  apiKeys: () => request<ApiKey[]>("/api-keys"),
  createApiKey: (name: string, role: string) =>
    request<ApiKey & { key: string }>("/api-keys", { method: "POST", body: json({ name, role }) }),
  revokeApiKey: (id: number) => request<ApiKey>(`/api-keys/${id}`, { method: "DELETE" }),
  webhookEvents: () => request<Record<string, string>>("/webhooks/events"),
  webhooks: () => request<Webhook[]>("/webhooks"),
  createWebhook: (url: string, events: string[]) =>
    request<Webhook & { secret: string }>("/webhooks", { method: "POST", body: json({ url, events }) }),
  updateWebhook: (id: number, body: Partial<{ url: string; events: string[]; is_active: boolean }>) =>
    request<Webhook>(`/webhooks/${id}`, { method: "PATCH", body: json(body) }),
  deleteWebhook: (id: number) => request<void>(`/webhooks/${id}`, { method: "DELETE" }),
  testWebhook: (id: number) => request<WebhookDelivery>(`/webhooks/${id}/test`, { method: "POST" }),
  webhookDeliveries: (id: number) => request<WebhookDelivery[]>(`/webhooks/${id}/deliveries`),
  createExport: (body: { catalog_id: number; language: string; format: string; integration_id?: number | null }) =>
    request<ExportJob>("/exports", { method: "POST", body: json(body) }),
  exports: (catalogId?: number) => request<ExportJob[]>(`/exports${catalogId ? `?catalog_id=${catalogId}` : ""}`),
  downloadExport: async (id: number) => {
    const res = await fetch(`/api/exports/${id}/download`, { headers: { Authorization: `Bearer ${auth.token}` } });
    if (!res.ok) throw new ApiError(res.status, "Download failed");
    const blob = await res.blob();
    const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? `export_${id}.csv`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  },
};

export interface JobInput {
  catalog_id: number;
  preset_id?: number | null;
  engine?: string | null;
  languages: string[];
  product_ids?: number[] | null;
  only_missing?: boolean;
  extra_instructions?: string;
  variants?: number;
  use_images?: boolean;
}
