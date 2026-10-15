import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, CatalogDetail, Job } from "../api";
import {
  can, EngineSelect, Empty, ErrorAlert, formatDate, Loading, minutes, money, pickDefaultEngine, ProgressBar, SeoScore,
  StatusBadge, useMe, useOptions,
} from "../components";
import { useTeam } from "../Collaboration";
import { Icon } from "../icons";

type Tab = "products" | "generate" | "export" | "jobs";

export default function CatalogDetailPage() {
  const catalogId = Number(useParams().catalogId);
  const { data: me } = useMe();
  const [params] = useSearchParams();
  const initialTab = params.get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(initialTab && ["products", "generate", "export", "jobs"].includes(initialTab) ? initialTab : "products");
  const [selected, setSelected] = useState<number[]>([]);
  const [language, setLanguage] = useState("en");

  const catalogQuery = useQuery({ queryKey: ["catalog", catalogId], queryFn: () => api.catalog(catalogId) });
  const jobsQuery = useQuery({
    queryKey: ["jobs", catalogId],
    queryFn: () => api.jobs(catalogId),
    refetchInterval: (q) => (q.state.data?.some((j) => j.status === "running" || j.status === "pending") ? 1500 : false),
  });
  const activeJob = jobsQuery.data?.find((j) => j.status === "running" || j.status === "pending");

  // Refresh product data while a job runs and once it finishes.
  const queryClient = useQueryClient();
  useEffect(() => {
    queryClient.invalidateQueries({ queryKey: ["products", catalogId] });
    queryClient.invalidateQueries({ queryKey: ["catalog", catalogId] });
  }, [activeJob?.completed, activeJob?.status, jobsQuery.data?.[0]?.status, catalogId, queryClient]);

  if (catalogQuery.isLoading) return <Loading />;
  if (catalogQuery.error || !catalogQuery.data) return <ErrorAlert error={catalogQuery.error} />;
  const catalog = catalogQuery.data;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{catalog.name}</h1>
          <p className="muted">
            {catalog.stats.total_products} products · avg SEO score {catalog.stats.avg_seo_score ?? "—"}
          </p>
        </div>
        {can(me, "edit") && (
          <button className="primary" onClick={() => setTab("generate")} disabled={!!activeJob}>
            Generate descriptions
          </button>
        )}
      </div>

      <LanguageStats catalog={catalog} />
      {activeJob && <JobProgress job={activeJob} />}

      <div className="tabs" style={{ marginTop: 16 }}>
        {(["products", "generate", "export", "jobs"] as Tab[]).map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {{ products: "Products & review", generate: "Generate", export: "Export", jobs: "Job history" }[t]}
          </button>
        ))}
      </div>

      {tab === "products" && (
        <ProductsTab
          catalogId={catalogId}
          language={language}
          setLanguage={setLanguage}
          selected={selected}
          setSelected={setSelected}
          onGenerateSelected={() => setTab("generate")}
        />
      )}
      {tab === "generate" && (
        <GenerateTab catalogId={catalogId} selected={selected} clearSelected={() => setSelected([])} activeJob={activeJob} onStarted={() => setTab("products")} />
      )}
      {tab === "export" && <ExportTab catalogId={catalogId} />}
      {tab === "jobs" && <JobsTab jobs={jobsQuery.data ?? []} />}
    </>
  );
}

function LanguageStats({ catalog }: { catalog: CatalogDetail }) {
  const { data: options } = useOptions();
  const entries = Object.entries(catalog.stats.by_language);
  if (!entries.length) return null;
  return (
    <div className="grid grid-4">
      {entries.map(([lang, s]) => {
        const done = s.approved;
        return (
          <div className="card" key={lang}>
            <div className="stat-label">{options?.languages[lang] ?? lang}</div>
            <div className="row small" style={{ margin: "6px 0" }}>
              <span className="badge draft">{s.draft} draft</span>
              {(s.in_review ?? 0) > 0 && <span className="badge in_review">{s.in_review} in review</span>}
              <span className="badge approved">{s.approved} approved</span>
              {s.rejected > 0 && <span className="badge rejected">{s.rejected} rejected</span>}
            </div>
            <ProgressBar value={done} total={catalog.stats.total_products} />
            <div className="muted small">{done}/{catalog.stats.total_products} approved</div>
          </div>
        );
      })}
    </div>
  );
}

function JobProgress({ job }: { job: Job }) {
  const queryClient = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => api.cancelJob(job.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", job.catalog_id] }),
  });
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row">
        <h3 style={{ margin: 0 }}>Generating descriptions… (job #{job.id})</h3>
        <div className="spacer" />
        <button className="small danger" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel</button>
      </div>
      <div style={{ margin: "10px 0 4px" }}>
        <ProgressBar value={job.completed + job.failed} total={job.total} />
      </div>
      <div className="muted small">
        {job.completed} of {job.total} done{job.failed ? ` · ${job.failed} failed` : ""} · cost so far {money(job.cost_usd)}
      </div>
    </div>
  );
}

function ProductsTab(props: {
  catalogId: number;
  language: string;
  setLanguage: (l: string) => void;
  selected: number[];
  setSelected: (ids: number[]) => void;
  onGenerateSelected: () => void;
}) {
  const { catalogId, language, setLanguage, selected, setSelected } = props;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: options } = useOptions();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState(() => new URLSearchParams(window.location.search).get("assignee") ?? "");
  const { data: team } = useTeam();
  const [page, setPage] = useState(1);
  const [minScore, setMinScore] = useState(70);
  const [message, setMessage] = useState<string | null>(null);
  const pageSize = 25;

  const { data, isLoading, error } = useQuery({
    queryKey: ["products", catalogId, language, status, search, assignee, page],
    queryFn: () => api.products(catalogId, { language, status, search, assignee, page, page_size: pageSize }),
    placeholderData: (prev) => prev,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["products", catalogId] });
    queryClient.invalidateQueries({ queryKey: ["catalog", catalogId] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const bulk = useMutation({
    mutationFn: (action: "approve" | "reject") => {
      const ids = (data?.items ?? [])
        .filter((p) => selected.includes(p.id) && p.current[language])
        .map((p) => p.current[language].id);
      return api.bulkReview(ids, action);
    },
    onSuccess: (r) => {
      setMessage(`${r.updated} descriptions updated` + (r.awaiting_final_approval ? ` (${r.awaiting_final_approval} now wait for final approval by an admin)` : "")
        + (r.skipped ? `; ${r.skipped} skipped (waiting for an admin)` : ""));
      setSelected([]);
      refresh();
    },
  });
  const assignSelected = useMutation({
    mutationFn: (userId: number | null) => api.bulkAssign(catalogId, selected, userId),
    onSuccess: (r) => { setMessage(`${r.updated} products assigned`); setSelected([]); refresh(); },
  });
  const approveDrafts = useMutation({
    mutationFn: (skip_flagged: boolean) => api.approveDrafts(catalogId, { language, min_seo_score: minScore, skip_flagged }),
    onSuccess: (r) => {
      setMessage(`${r.updated} approved` + (r.awaiting_final_approval ? ` — ${r.awaiting_final_approval} now wait for final approval by an admin` : ""));
      refresh();
    },
  });

  const items = data?.items ?? [];
  const allSelected = items.length > 0 && items.every((p) => selected.includes(p.id));
  const toggleAll = () =>
    setSelected(allSelected ? selected.filter((id) => !items.some((p) => p.id === id)) : [...new Set([...selected, ...items.map((p) => p.id)])]);
  const toggle = (id: number) => setSelected(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  const pages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 12 }}>
        <select style={{ width: 170 }} value={language} onChange={(e) => { setLanguage(e.target.value); setPage(1); }}>
          {Object.entries(options?.languages ?? { en: "English" }).map(([code, name]) => (
            <option key={code} value={code}>{name}</option>
          ))}
        </select>
        <select style={{ width: 170 }} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All statuses</option>
          <option value="missing">Not generated</option>
          <option value="draft">Awaiting review</option>
          <option value="in_review">Awaiting final approval</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="warnings">Flagged (warnings)</option>
        </select>
        <select style={{ width: 170 }} value={assignee} onChange={(e) => { setAssignee(e.target.value); setPage(1); }}>
          <option value="">Anyone</option>
          <option value="me">Assigned to me</option>
          <option value="none">Unassigned</option>
          {team?.filter((u) => u.is_active).map((u) => <option key={u.id} value={String(u.id)}>{u.full_name}</option>)}
        </select>
        <input style={{ width: 220 }} placeholder="Search SKU or name…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        <div className="spacer" />
        {can(me, "review") && (
          <>
            <span className="small muted">Approve drafts with SEO ≥</span>
            <input type="number" min={0} max={100} style={{ width: 70 }} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
            <button
              className="success"
              disabled={approveDrafts.isPending}
              onClick={() => confirm(`Approve all ${options?.languages[language] ?? language} drafts with SEO score ≥ ${minScore} that have no warnings?`) && approveDrafts.mutate(true)}
            >
              Bulk approve
            </button>
          </>
        )}
      </div>

      {selected.length > 0 && (
        <div className="alert info row">
          <b>{selected.length} selected</b>
          <div className="spacer" />
          {can(me, "edit") && <button className="small" onClick={props.onGenerateSelected}>Generate for selected</button>}
          {can(me, "edit") && (
            <select className="small" style={{ width: 170, padding: "4px 8px" }} value=""
              onChange={(e) => e.target.value && assignSelected.mutate(e.target.value === "none" ? null : Number(e.target.value))}>
              <option value="">Assign to…</option>
              <option value="none">Nobody</option>
              {team?.filter((u) => u.is_active).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
            </select>
          )}
          {can(me, "review") && (
            <>
              <button className="small success" onClick={() => bulk.mutate("approve")}>Approve</button>
              <button className="small danger" onClick={() => bulk.mutate("reject")}>Reject</button>
            </>
          )}
          <button className="small" onClick={() => setSelected([])}>Clear</button>
        </div>
      )}
      {message && <div className="alert success">{message}</div>}
      <ErrorAlert error={error || bulk.error || approveDrafts.error || assignSelected.error} />

      {isLoading ? (
        <Loading />
      ) : !items.length ? (
        <Empty>No products match these filters.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: 32 }}><input type="checkbox" checked={allSelected} onChange={toggleAll} /></th>
                <th>SKU</th>
                <th>Product</th>
                <th>Generated title</th>
                <th>SEO</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p) => {
                const v = p.current[language];
                return (
                  <tr key={p.id} className="clickable" onClick={() => navigate(`/catalogs/${catalogId}/products/${p.id}?lang=${language}`)}>
                    <td onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={selected.includes(p.id)} onChange={() => toggle(p.id)} />
                    </td>
                    <td className="small">{p.sku}</td>
                    <td>
                      <b>{p.name}</b>
                      <div className="muted small">{[p.brand, p.category].filter(Boolean).join(" · ")}</div>
                    </td>
                    <td>
                      {v ? v.title : <span className="muted">—</span>}
                      {v?.warnings.length ? <div><span className="badge warn" title={v.warnings.map((w) => w.message).join("\n")}><Icon name="alert" size={12} />{v.warnings.length} warning{v.warnings.length > 1 ? "s" : ""}</span></div> : null}
                    </td>
                    <td>{v ? <SeoScore score={v.seo_score} /> : "—"}</td>
                    <td>
                      {v ? <StatusBadge status={v.status} /> : <span className="badge">not generated</span>}
                      {p.assignee_id && <div className="muted small">→ {team?.find((u) => u.id === p.assignee_id)?.full_name ?? "assigned"}</div>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <span className="muted small">{data?.total ?? 0} products</span>
        <div className="spacer" />
        <button className="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span className="small">Page {page} of {pages}</span>
        <button className="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}

const MAIN_LANGUAGES = ["en", "ur", "ar", "hi"];

function GenerateTab(props: { catalogId: number; selected: number[]; clearSelected: () => void; activeJob?: Job; onStarted: () => void }) {
  const { catalogId, selected } = props;
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: options } = useOptions();
  const { data: presets } = useQuery({ queryKey: ["presets"], queryFn: api.presets });
  const [presetId, setPresetId] = useState<number | null>(null);
  const [languages, setLanguages] = useState<string[]>(["en"]);
  const [scope, setScope] = useState<"missing" | "all" | "selected">(selected.length ? "selected" : "missing");
  const [extra, setExtra] = useState("");
  const [engine, setEngine] = useState<string | undefined>();
  const [variants, setVariants] = useState(1);
  const [useImages, setUseImages] = useState(true);
  const [allLanguages, setAllLanguages] = useState(false);

  useEffect(() => {
    if (presetId === null && presets?.length) setPresetId(presets[0].id);
  }, [presets, presetId]);
  useEffect(() => {
    if (engine === undefined) setEngine(pickDefaultEngine(options));
  }, [options, engine]);

  const body = {
    catalog_id: catalogId,
    preset_id: presetId,
    engine: engine ?? null,
    languages,
    product_ids: scope === "selected" ? selected : null,
    only_missing: scope === "missing",
    extra_instructions: extra,
    variants,
    use_images: useImages,
  };
  const estimate = useQuery({
    queryKey: ["estimate", body],
    queryFn: () => api.estimateJob(body),
    enabled: !!engine && languages.length > 0 && (scope !== "selected" || selected.length > 0),
  });
  const start = useMutation({
    mutationFn: () => api.createJob(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs", catalogId] });
      props.clearSelected();
      props.onStarted();
    },
  });

  if (!can(me, "edit")) return <div className="alert warning">Only editors can generate descriptions.</div>;
  const preset = presets?.find((p) => p.id === presetId);
  const toggleLang = (code: string) =>
    setLanguages(languages.includes(code) ? languages.filter((l) => l !== code) : [...languages, code]);
  const overQuota = estimate.data && estimate.data.items > estimate.data.remaining_quota;

  return (
    <div className="grid grid-2">
      <div className="card">
        <h2>What to generate</h2>
        <div className="field">
          <label>Products</label>
          <select value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
            <option value="missing">Only products without a description yet</option>
            <option value="all">All products (creates new versions for existing ones)</option>
            <option value="selected" disabled={!selected.length}>Selected products ({selected.length})</option>
          </select>
        </div>
        <div className="field">
          <label>Languages</label>
          <div className="lang-picker">
            {Object.entries(options?.languages ?? {})
              .filter(([code]) => allLanguages || MAIN_LANGUAGES.includes(code) || languages.includes(code))
              .map(([code, name]) => (
                <label key={code} className={languages.includes(code) ? "on" : ""}>
                  <input type="checkbox" checked={languages.includes(code)} onChange={() => toggleLang(code)} /> {name}
                </label>
              ))}
            {!allLanguages && options && (
              <button className="link small" onClick={() => setAllLanguages(true)}>
                + {Math.max(0, Object.keys(options?.languages ?? {}).length - MAIN_LANGUAGES.length)} more languages
              </button>
            )}
          </div>
        </div>
        <div className="field">
          <label>Tone &amp; style</label>
          <select value={presetId ?? ""} onChange={(e) => setPresetId(Number(e.target.value))}>
            {presets?.map((p) => (
              <option key={p.id} value={p.id}>{p.name} ({p.tone}, {p.length}){p.is_builtin ? "" : " – custom"}</option>
            ))}
          </select>
          {preset && <p className="muted small" style={{ marginTop: 4 }}>{options?.tones[preset.tone]}</p>}
          <Link to="/presets" className="small">Manage presets →</Link>
        </div>

        <details className="advanced">
          <summary>
            Advanced options
            <span className="muted small">AI engine, variants, photos, extra instructions</span>
          </summary>
          <div className="field">
            <label>AI engine</label>
            <EngineSelect value={engine} onChange={setEngine} />
          </div>
          <div className="grid grid-2">
            <div className="field">
              <label>Versions per product</label>
              <select value={variants} onChange={(e) => setVariants(Number(e.target.value))}>
                <option value={1}>1 (standard)</option>
                <option value={2}>2 variants (best one is selected)</option>
                <option value={3}>3 variants (best one is selected)</option>
              </select>
              <p className="muted small" style={{ marginTop: 4 }}>You can compare variants and switch on the review page.</p>
            </div>
            <div className="field">
              <label>Product photos</label>
              <label className="checkbox">
                <input type="checkbox" checked={useImages} onChange={(e) => setUseImages(e.target.checked)} />
                Let the AI look at product photos
              </label>
              <p className="muted small" style={{ marginTop: 4 }}>The AI studies each photo first (color, shape, visible details). Works with every engine when Gemini or Claude is available.</p>
            </div>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Extra instructions for this batch</label>
            <textarea value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="e.g. Mention free shipping is not available. Use British spelling." />
          </div>
        </details>
      </div>

      <div className="card">
        <h2>Summary</h2>
        {estimate.isLoading ? (
          <Loading />
        ) : estimate.data ? (
          <dl className="kv">
            <dt>Descriptions</dt><dd><b>{estimate.data.items}</b> ({languages.length} language{languages.length > 1 ? "s" : ""}{variants > 1 ? `, ${variants} variants each` : ""})</dd>
            {useImages && <><dt>With photos</dt><dd>{estimate.data.products_with_images} products</dd></>}
            <dt>AI model</dt><dd>{estimate.data.model}</dd>
            <dt>Estimated cost</dt><dd>{estimate.data.estimated_cost_usd === 0 ? <b style={{ color: "var(--success)" }}>Free</b> : `~${money(estimate.data.estimated_cost_usd)}`}</dd>
            <dt>Estimated time</dt><dd>{minutes(estimate.data.estimated_minutes)}</dd>
            <dt>Plan remaining</dt><dd>{estimate.data.remaining_quota} this month</dd>
          </dl>
        ) : (
          <p className="muted">Choose at least one language.</p>
        )}
        {overQuota && <div className="alert warning" style={{ marginTop: 12 }}>This exceeds your remaining monthly quota.</div>}
        {estimate.data?.items === 0 && scope === "missing" && (
          <div className="alert info" style={{ marginTop: 12 }}>
            Every product already has a description in the chosen language(s). To rewrite them, choose
            "All products" (old versions stay in the history), select specific products, or pick another language.
          </div>
        )}
        {variants > 1 && (engine === "gemini" || engine === "groq") && (
          <div className="alert info" style={{ marginTop: 12 }}>
            Each variant is a separate AI request. Free tiers have per-minute and daily limits, so 1 variant is
            recommended with free AI.
          </div>
        )}
        {props.activeJob && <div className="alert info" style={{ marginTop: 12 }}>A job is already running for this catalog.</div>}
        {engine?.startsWith("ollama") && estimate.data && estimate.data.estimated_minutes > 30 && (
          <div className="alert info" style={{ marginTop: 12 }}>
            Local AI is free but slow on this computer. Keep the app and Ollama open until the job finishes — you can
            keep reviewing finished products meanwhile. Tip: start with a few selected products.
          </div>
        )}
        <ErrorAlert error={estimate.error || start.error} />
        <button
          className="primary"
          style={{ marginTop: 12, width: "100%" }}
          disabled={!estimate.data?.items || !!overQuota || start.isPending || !!props.activeJob}
          onClick={() => start.mutate()}
        >
          {start.isPending ? "Starting…" : `Generate ${estimate.data?.items ?? 0} descriptions`}
        </button>
        <p className="muted small" style={{ marginTop: 10 }}>
          Every description gets an SEO score and automatic checks for invented facts and duplicate content. Nothing is
          published until a reviewer approves it.
        </p>
      </div>
    </div>
  );
}

const FORMAT_LABELS: Record<string, string> = {
  csv_generic: "CSV (all fields)",
  csv_shopify: "CSV for Shopify import",
  csv_woocommerce: "CSV for WooCommerce import",
  csv_amazon: "CSV for Amazon (content columns)",
  csv_ebay: "CSV for eBay (content columns)",
  csv_daraz: "CSV for Daraz (content columns)",
  shopify: "Push to Shopify store",
  woocommerce: "Push to WooCommerce store",
};

export function ExportTab({ catalogId }: { catalogId: number }) {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: options } = useOptions();
  const { data: integrations } = useQuery({ queryKey: ["integrations"], queryFn: api.integrations });
  const exportsQuery = useQuery({
    queryKey: ["exports", catalogId],
    queryFn: () => api.exports(catalogId),
    refetchInterval: (q) => (q.state.data?.some((e) => e.status === "running" || e.status === "pending") ? 1500 : false),
  });
  const [language, setLanguage] = useState("en");
  const [format, setFormat] = useState("csv_generic");
  const [integrationId, setIntegrationId] = useState<number | null>(null);
  const isPlatform = format === "shopify" || format === "woocommerce";
  const matching = integrations?.filter((i) => i.platform === format) ?? [];

  const create = useMutation({
    mutationFn: () =>
      api.createExport({ catalog_id: catalogId, language, format, integration_id: isPlatform ? integrationId ?? matching[0]?.id : null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exports", catalogId] }),
  });
  const download = useMutation({ mutationFn: api.downloadExport });

  return (
    <>
      {can(me, "edit") && (
        <div className="card">
          <h2>New export</h2>
          <p className="muted">Exports include only <b>approved</b> descriptions (the latest approved version of each product).</p>
          <div className="grid grid-4">
            <div className="field">
              <label>Language</label>
              <select value={language} onChange={(e) => setLanguage(e.target.value)}>
                {Object.entries(options?.languages ?? {}).map(([c, n]) => <option key={c} value={c}>{n}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Format</label>
              <select value={format} onChange={(e) => setFormat(e.target.value)}>
                {Object.entries(FORMAT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            {isPlatform && (
              <div className="field">
                <label>Store connection</label>
                {matching.length ? (
                  <select value={integrationId ?? matching[0].id} onChange={(e) => setIntegrationId(Number(e.target.value))}>
                    {matching.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                  </select>
                ) : (
                  <div className="small">No {format} connection. <Link to="/integrations">Add one →</Link></div>
                )}
              </div>
            )}
          </div>
          <ErrorAlert error={create.error || download.error} />
          <button className="primary" disabled={create.isPending || (isPlatform && !matching.length)} onClick={() => create.mutate()}>
            {isPlatform ? "Start push" : "Create export"}
          </button>
        </div>
      )}

      <div className="card">
        <h2>Exports</h2>
        {!exportsQuery.data?.length ? (
          <Empty>No exports yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Format</th><th>Language</th><th>Status</th><th>Result</th><th>Created</th><th /></tr></thead>
              <tbody>
                {exportsQuery.data.map((e) => (
                  <tr key={e.id}>
                    <td>{e.id}</td>
                    <td>{FORMAT_LABELS[e.format] ?? e.format}</td>
                    <td>{options?.languages[e.language] ?? e.language}</td>
                    <td><StatusBadge status={e.status} /></td>
                    <td className="small">
                      {e.succeeded}/{e.total} exported{e.failed ? `, ${e.failed} failed` : ""}
                      {e.errors.slice(0, 3).map((err, i) => <div key={i} className="muted">{err.sku ? `${err.sku}: ` : ""}{err.error}</div>)}
                    </td>
                    <td className="small">{formatDate(e.created_at)}</td>
                    <td>{e.file_key && <button className="small" onClick={() => download.mutate(e.id)}>Download</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function JobsTab({ jobs }: { jobs: Job[] }) {
  if (!jobs.length) return <div className="card"><Empty>No generation jobs yet.</Empty></div>;
  return (
    <div className="card">
      <div className="table-wrap">
        <table>
          <thead><tr><th>#</th><th>Status</th><th>Done</th><th>Failed</th><th>Languages</th><th>AI engine</th><th>Tokens</th><th>Cost</th><th>Started</th></tr></thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.id}</td>
                <td><StatusBadge status={j.status} /></td>
                <td>{j.completed}/{j.total}</td>
                <td>
                  {j.failed}
                  {j.errors.slice(0, 3).map((e, i) => <div key={i} className="small muted">{e.sku} {e.language}: {e.error}</div>)}
                </td>
                <td>{j.languages.join(", ")}</td>
                <td className="small">{j.engine ?? "default"}</td>
                <td className="small">{j.input_tokens.toLocaleString()} in / {j.output_tokens.toLocaleString()} out</td>
                <td>{money(j.cost_usd)}</td>
                <td className="small">{formatDate(j.started_at ?? j.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
