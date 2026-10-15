import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Webhook } from "../api";
import { can, ErrorAlert, formatDate, SettingsTabs, useMe } from "../components";

export default function DeveloperPage() {
  const { data: me } = useMe();
  if (!can(me, "admin")) return <div className="alert warning">Only admins can manage API keys and webhooks.</div>;
  return (
    <>
      <SettingsTabs />
      <div className="page-head">
        <div>
          <h1>API &amp; webhooks</h1>
          <p className="muted">
            Connect your ERP, PIM or other systems. Full API reference: <a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a>
          </p>
        </div>
      </div>
      <ApiKeys />
      <Webhooks />
    </>
  );
}

function ApiKeys() {
  const queryClient = useQueryClient();
  const { data: keys } = useQuery({ queryKey: ["api-keys"], queryFn: api.apiKeys });
  const [name, setName] = useState("");
  const [role, setRole] = useState("editor");
  const [created, setCreated] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["api-keys"] });
  const create = useMutation({
    mutationFn: () => api.createApiKey(name, role),
    onSuccess: (k) => { setCreated(k.key); setName(""); refresh(); },
  });
  const revoke = useMutation({ mutationFn: api.revokeApiKey, onSuccess: refresh });

  return (
    <div className="card">
      <h2>API keys</h2>
      <p className="muted small">
        Send the key in the <code>X-API-Key</code> header (or <code>Authorization: Bearer …</code>). A key acts with the chosen
        role inside this workspace. Example: <code>curl -H "X-API-Key: cai_…" {window.location.origin}/api/catalogs</code>
      </p>
      <ErrorAlert error={create.error || revoke.error} />
      {created && (
        <div className="alert success">
          Copy this key now — it will not be shown again:
          <div className="row" style={{ marginTop: 6 }}>
            <code style={{ wordBreak: "break-all" }}>{created}</code>
            <button className="small" onClick={() => navigator.clipboard?.writeText(created)}>Copy</button>
            <button className="small" onClick={() => setCreated(null)}>Done</button>
          </div>
        </div>
      )}
      <div className="row" style={{ marginBottom: 12 }}>
        <input style={{ maxWidth: 260 }} placeholder="Key name, e.g. ERP sync" value={name} onChange={(e) => setName(e.target.value)} />
        <select style={{ width: 140 }} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="editor">editor</option>
          <option value="reviewer">reviewer</option>
          <option value="admin">admin</option>
        </select>
        <button className="primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>Create key</button>
      </div>
      {keys?.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Key</th><th>Role</th><th>Last used</th><th>Created</th><th /></tr></thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} style={k.revoked_at ? { opacity: 0.5 } : undefined}>
                  <td>{k.name}</td>
                  <td><code>{k.prefix}…</code></td>
                  <td>{k.role}</td>
                  <td className="small">{formatDate(k.last_used_at)}</td>
                  <td className="small">{formatDate(k.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    {k.revoked_at ? <span className="badge rejected">revoked</span> : (
                      <button className="small danger" onClick={() => confirm(`Revoke "${k.name}"? Systems using it will stop working.`) && revoke.mutate(k.id)}>Revoke</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted small">No API keys yet.</p>}
    </div>
  );
}

function Webhooks() {
  const queryClient = useQueryClient();
  const { data: hooks } = useQuery({ queryKey: ["webhooks"], queryFn: api.webhooks });
  const { data: events } = useQuery({ queryKey: ["webhook-events"], queryFn: api.webhookEvents });
  const [url, setUrl] = useState("");
  const [selected, setSelected] = useState<string[]>(["job.completed"]);
  const [secret, setSecret] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["webhooks"] });
  const create = useMutation({
    mutationFn: () => api.createWebhook(url, selected),
    onSuccess: (h) => { setSecret(h.secret); setUrl(""); refresh(); },
  });
  const toggle = (e: string) => setSelected(selected.includes(e) ? selected.filter((x) => x !== e) : [...selected, e]);

  return (
    <div className="card">
      <h2>Webhooks</h2>
      <p className="muted small">
        We POST a JSON event to your URL. Verify the <code>X-Inkventory-Signature</code> header: it is
        <code> sha256=</code>HMAC-SHA256 of the raw body with your signing secret. Failed deliveries are retried 3 times.
      </p>
      <ErrorAlert error={create.error} />
      {secret && (
        <div className="alert success">
          Signing secret (shown once): <code style={{ wordBreak: "break-all" }}>{secret}</code>{" "}
          <button className="small" onClick={() => setSecret(null)}>Done</button>
        </div>
      )}
      <div className="field">
        <label>Endpoint URL</label>
        <input placeholder="https://your-system.com/webhooks/inkventory" value={url} onChange={(e) => setUrl(e.target.value)} />
      </div>
      <div className="lang-picker" style={{ marginBottom: 12 }}>
        {Object.entries(events ?? {}).map(([e, desc]) => (
          <label key={e} className={selected.includes(e) ? "on" : ""} title={desc}>
            <input type="checkbox" checked={selected.includes(e)} onChange={() => toggle(e)} /> {e}
          </label>
        ))}
      </div>
      <button className="primary" disabled={!url.startsWith("https://") && !url.startsWith("http://") || !selected.length || create.isPending}
        onClick={() => create.mutate()}>Add webhook</button>

      <div style={{ marginTop: 16 }}>
        {hooks?.map((h) => <WebhookRow key={h.id} hook={h} onChange={refresh} />)}
        {hooks?.length === 0 && <p className="muted small">No webhooks yet.</p>}
      </div>
    </div>
  );
}

function WebhookRow({ hook, onChange }: { hook: Webhook; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const deliveries = useQuery({ queryKey: ["deliveries", hook.id], queryFn: () => api.webhookDeliveries(hook.id), enabled: open });
  const test = useMutation({ mutationFn: () => api.testWebhook(hook.id), onSuccess: () => { setOpen(true); deliveries.refetch(); } });
  const toggleActive = useMutation({ mutationFn: () => api.updateWebhook(hook.id, { is_active: !hook.is_active }), onSuccess: onChange });
  const remove = useMutation({ mutationFn: () => api.deleteWebhook(hook.id), onSuccess: onChange });

  return (
    <div className="version-item">
      <div className="row">
        <code style={{ wordBreak: "break-all" }}>{hook.url}</code>
        {!hook.is_active && <span className="badge">paused</span>}
        <div className="spacer" />
        <button className="small" disabled={test.isPending} onClick={() => test.mutate()}>Send test</button>
        <button className="small" onClick={() => setOpen(!open)}>{open ? "Hide" : "Deliveries"}</button>
        <button className="small" onClick={() => toggleActive.mutate()}>{hook.is_active ? "Pause" : "Resume"}</button>
        <button className="small danger" onClick={() => confirm("Delete this webhook?") && remove.mutate()}>Delete</button>
      </div>
      <div className="small muted">{hook.events.join(", ")}</div>
      {open && (
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Event</th><th>Result</th><th>Attempts</th><th>Time</th></tr></thead>
          <tbody>
            {deliveries.data?.map((d) => (
              <tr key={d.id}>
                <td>{d.event}</td>
                <td>{d.success ? <span className="badge approved">{d.status_code}</span>
                  : <span className="badge rejected" title={d.error ?? ""}>{d.status_code ?? "error"}</span>}
                  {!d.success && d.error && <div className="small muted">{d.error.slice(0, 120)}</div>}</td>
                <td>{d.attempts}</td>
                <td className="small">{formatDate(d.created_at)}</td>
              </tr>
            ))}
            {deliveries.data?.length === 0 && <tr><td colSpan={4} className="muted small">No deliveries yet.</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  );
}
