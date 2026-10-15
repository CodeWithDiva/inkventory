import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, auth } from "../api";
import { can, ErrorAlert, Loading, SettingsTabs, useMe } from "../components";

export default function SettingsPage() {
  const { data: me } = useMe();
  const admin = can(me, "admin");
  return (
    <>
      <SettingsTabs />
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="muted">Workspace and account settings.</p>
        </div>
      </div>
      {admin ? <OrganizationSettings /> : <div className="alert info">Only admins can change workspace settings.</div>}
      <ChangePassword />
    </>
  );
}

function OrganizationSettings() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["org-settings"], queryFn: api.orgSettings });
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [test, setTest] = useState<{ ok: boolean; detail: string } | null>(null);
  useEffect(() => setName(data?.name ?? ""), [data?.name]);

  const refresh = (msg: string) => {
    setMessage(msg);
    setTest(null);
    queryClient.invalidateQueries({ queryKey: ["org-settings"] });
    queryClient.invalidateQueries({ queryKey: ["options"] });
    queryClient.invalidateQueries({ queryKey: ["me"] });
  };
  const save = useMutation({
    mutationFn: (body: Parameters<typeof api.updateOrgSettings>[0]) => api.updateOrgSettings(body),
    onSuccess: (_, body) =>
      refresh(body.anthropic_api_key === "" ? "Key removed" : body.anthropic_api_key ? "Key saved (encrypted)" : "Saved"),
  });
  const testKey = useMutation({ mutationFn: api.testOwnKey, onSuccess: setTest });

  if (isLoading || !data) return <Loading />;
  return (
    <>
      {message && <div className="alert success">{message}</div>}
      <ErrorAlert error={save.error || testKey.error} />
      <div className="card">
        <h2>Workspace</h2>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>Company / store name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <button disabled={!name.trim() || name === data.name || save.isPending} onClick={() => save.mutate({ name })}>Save</button>
        </div>
        <label className="checkbox" style={{ marginTop: 14 }}>
          <input
            type="checkbox"
            checked={data.notify_on_job_complete}
            onChange={(e) => save.mutate({ notify_on_job_complete: e.target.checked })}
          />
          Email me when a generation job finishes
        </label>
      </div>

      <div className="card">
        <h2>Claude AI (your own key)</h2>
        <p className="muted">
          Free AI is included for everyone. For Claude (best quality in every language), add your company's own key from{" "}
          <a href="https://console.anthropic.com" target="_blank" rel="noreferrer">console.anthropic.com</a> (Settings → API keys,
          with some credits). Claude is then available on any plan and is billed directly by Anthropic to your account.
          The key is stored encrypted and never shown again.
        </p>
        {data.has_own_anthropic_key ? (
          <>
            <div className="row">
              <span>Saved key: <code>{data.anthropic_key_hint}</code></span>
              <div className="spacer" />
              <button className="small" disabled={testKey.isPending} onClick={() => testKey.mutate()}>
                {testKey.isPending ? "Testing…" : "Test key"}
              </button>
              <button className="small danger" onClick={() => confirm("Remove your Claude key?") && save.mutate({ anthropic_api_key: "" })}>
                Remove
              </button>
            </div>
            {test && <div className={`alert ${test.ok ? "success" : "error"}`} style={{ marginTop: 10 }}>{test.detail}</div>}
          </>
        ) : (
          <div className="row">
            <input type="password" style={{ flex: 1 }} placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="primary" disabled={!key.trim() || save.isPending}
              onClick={() => { save.mutate({ anthropic_api_key: key.trim() }); setKey(""); }}>
              Save key
            </button>
          </div>
        )}
      </div>
    </>
  );
}

function ChangePassword() {
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [done, setDone] = useState(false);
  const change = useMutation({
    mutationFn: () => api.changePassword(form.current, form.next),
    onSuccess: ({ access_token }) => {
      auth.set(access_token);
      setForm({ current: "", next: "", confirm: "" });
      setDone(true);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setDone(false);
    change.mutate();
  };
  const mismatch = form.confirm && form.next !== form.confirm;

  return (
    <form className="card" onSubmit={submit}>
      <h2>Change password</h2>
      {done && <div className="alert success">Password changed. Other devices have been signed out.</div>}
      <ErrorAlert error={change.error} />
      <div className="grid grid-4">
        <div className="field"><label>Current password</label>
          <input type="password" required value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} /></div>
        <div className="field"><label>New password</label>
          <input type="password" required minLength={8} value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} /></div>
        <div className="field"><label>Repeat new password</label>
          <input type="password" required minLength={8} value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} /></div>
      </div>
      {mismatch && <p className="small" style={{ color: "var(--danger)" }}>The new passwords do not match.</p>}
      <button className="primary" disabled={!!mismatch || change.isPending}>Change password</button>
    </form>
  );
}
