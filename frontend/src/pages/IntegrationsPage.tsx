import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, Integration } from "../api";
import { can, Empty, ErrorAlert, formatDate, Loading, StatusBadge, SettingsTabs, useMe } from "../components";

const PLATFORMS = {
  shopify: {
    label: "Shopify",
    fields: [
      { key: "shop_domain", label: "Shop domain", placeholder: "your-store.myshopify.com" },
      { key: "access_token", label: "Admin API access token", placeholder: "shpat_…", secret: true },
      { key: "api_version", label: "API version (optional)", placeholder: "2026-07" },
    ],
    help: "Create a custom app in Shopify Admin → Settings → Apps → Develop apps, with the write_products scope.",
  },
  woocommerce: {
    label: "WooCommerce",
    fields: [
      { key: "store_url", label: "Store URL", placeholder: "https://yourstore.com" },
      { key: "consumer_key", label: "Consumer key", placeholder: "ck_…", secret: true },
      { key: "consumer_secret", label: "Consumer secret", placeholder: "cs_…", secret: true },
    ],
    help: "Create a REST API key with Read/Write permission in WooCommerce → Settings → Advanced → REST API.",
  },
} as const;

type Platform = keyof typeof PLATFORMS;

export default function IntegrationsPage() {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data, isLoading } = useQuery({ queryKey: ["integrations"], queryFn: api.integrations });
  const [adding, setAdding] = useState(false);
  const [platform, setPlatform] = useState<Platform>("shopify");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<Record<number, { ok: boolean; detail: string }>>({});

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["integrations"] });
  const create = useMutation({
    mutationFn: () =>
      api.createIntegration({ platform, name, config: Object.fromEntries(Object.entries(config).filter(([, v]) => v.trim())) }),
    onSuccess: () => {
      setAdding(false);
      setName("");
      setConfig({});
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: api.deleteIntegration, onSuccess: refresh });
  const test = useMutation({
    mutationFn: api.testIntegration,
    onSuccess: (r, id) => setTestResult((prev) => ({ ...prev, [id]: r })),
  });
  const admin = can(me, "admin");

  return (
    <>
      <SettingsTabs />
      <div className="page-head">
        <div>
          <h1>Store connections</h1>
          <p className="muted">Push approved descriptions straight to your store. Products are matched by SKU.</p>
        </div>
        {admin && !adding && <button className="primary" onClick={() => setAdding(true)}>Add connection</button>}
      </div>
      <ErrorAlert error={create.error || remove.error || test.error} />

      {adding && (
        <div className="card">
          <h2>New connection</h2>
          <div className="grid grid-2">
            <div className="field">
              <label>Platform</label>
              <select value={platform} onChange={(e) => { setPlatform(e.target.value as Platform); setConfig({}); }}>
                {Object.entries(PLATFORMS).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Main store" />
            </div>
            {PLATFORMS[platform].fields.map((f) => (
              <div className="field" key={f.key}>
                <label>{f.label}</label>
                <input type={"secret" in f ? "password" : "text"} placeholder={f.placeholder} value={config[f.key] ?? ""}
                  onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })} />
              </div>
            ))}
          </div>
          <p className="muted small">{PLATFORMS[platform].help}</p>
          <div className="row">
            <button className="primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>Save</button>
            <button onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="card">
        {isLoading ? <Loading /> : !data?.length ? (
          <Empty>No store connections yet. You can still export CSV files from any catalog.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Platform</th><th>Details</th><th>Added</th><th /></tr></thead>
              <tbody>
                {data.map((i) => (
                  <tr key={i.id}>
                    <td><b>{i.name}</b></td>
                    <td>{PLATFORMS[i.platform]?.label ?? i.platform}</td>
                    <td className="small">
                      {Object.entries(i.config).map(([k, v]) => <div key={k}><span className="muted">{k}:</span> {v}</div>)}
                      {testResult[i.id] && (
                        <div className={`alert ${testResult[i.id].ok ? "success" : "error"}`} style={{ marginTop: 6, marginBottom: 0 }}>
                          {testResult[i.id].ok ? `Connected: ${testResult[i.id].detail}` : testResult[i.id].detail}
                        </div>
                      )}
                    </td>
                    <td className="small">{formatDate(i.created_at)}</td>
                    <td style={{ textAlign: "right" }}>
                      {admin && (
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          <button className="small" disabled={test.isPending} onClick={() => test.mutate(i.id)}>Test</button>
                          <button className="small danger" onClick={() => confirm(`Remove "${i.name}"?`) && remove.mutate(i.id)}>Remove</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {data?.map((i) => <SyncCard key={i.id} integration={i} canEdit={can(me, "edit")} admin={admin} />)}
    </>
  );
}

function SyncCard({ integration, canEdit, admin }: { integration: Integration; canEdit: boolean; admin: boolean }) {
  const queryClient = useQueryClient();
  const { data: catalogs } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs });
  const runs = useQuery({
    queryKey: ["syncs", integration.id],
    queryFn: () => api.syncRuns(integration.id),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running" || r.status === "pending") ? 2000 : false),
  });
  const [catalogId, setCatalogId] = useState<number | null>(integration.sync_catalog_id);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["syncs", integration.id] });
    queryClient.invalidateQueries({ queryKey: ["integrations"] });
    queryClient.invalidateQueries({ queryKey: ["catalogs"] });
  };
  const start = useMutation({ mutationFn: () => api.startSync(integration.id, catalogId), onSuccess: refresh });
  const schedule = useMutation({
    mutationFn: (hours: number | null) => api.syncSettings(integration.id, { auto_sync_hours: hours }),
    onSuccess: refresh,
  });
  const latest = runs.data?.[0];
  const busy = latest && (latest.status === "running" || latest.status === "pending");

  return (
    <div className="card">
      <div className="row">
        <h3 style={{ margin: 0 }}>Import products from {integration.name}</h3>
        <div className="spacer" />
        {integration.last_synced_at && <span className="muted small">Last sync {formatDate(integration.last_synced_at)}</span>}
      </div>
      <p className="muted small">
        Pulls products (title, SKU, type, brand, tags, options, photos and the current description) into a catalog.
        Existing products are updated by SKU; generated descriptions are kept.
      </p>
      <ErrorAlert error={start.error || schedule.error} />
      <div className="row">
        {canEdit && (
          <>
            <select style={{ width: 240 }} value={catalogId ?? ""} onChange={(e) => setCatalogId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">New catalog</option>
              {catalogs?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <button className="primary" disabled={!!busy || start.isPending} onClick={() => start.mutate()}>
              {busy ? "Importing…" : "Import now"}
            </button>
          </>
        )}
        {admin && (
          <>
            <span className="small muted" style={{ marginLeft: 12 }}>Automatic sync:</span>
            <select style={{ width: 150 }} value={integration.auto_sync_hours ?? ""}
              onChange={(e) => schedule.mutate(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Off</option>
              <option value={6}>Every 6 hours</option>
              <option value={24}>Daily</option>
              <option value={168}>Weekly</option>
            </select>
          </>
        )}
      </div>
      {runs.data && runs.data.length > 0 && (
        <table style={{ marginTop: 12 }}>
          <thead><tr><th>Started</th><th>Trigger</th><th>Status</th><th>New</th><th>Updated</th><th>Catalog</th></tr></thead>
          <tbody>
            {runs.data.slice(0, 5).map((r) => (
              <tr key={r.id}>
                <td className="small">{formatDate(r.created_at)}</td>
                <td className="small">{r.trigger}</td>
                <td><StatusBadge status={r.status} />
                  {r.errors.slice(-1).map((e, i) => <div key={i} className="small muted">{e.sku ? `${e.sku}: ` : ""}{e.error}</div>)}</td>
                <td>{r.imported}</td>
                <td>{r.updated}</td>
                <td>{r.catalog_id ? <Link to={`/catalogs/${r.catalog_id}`}>Open</Link> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
