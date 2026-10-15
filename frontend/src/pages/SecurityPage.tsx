import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, auth } from "../api";
import { can, ErrorAlert, formatDate, Loading, SettingsTabs, useMe } from "../components";

export default function SecurityPage() {
  const { data: me } = useMe();
  if (!me) return <Loading />;
  return (
    <>
      <SettingsTabs />
      <div className="page-head">
        <div>
          <h1>Security</h1>
          <p className="muted">Two-step login, signed-in devices and your data.</p>
        </div>
      </div>
      <TwoStepLogin />
      {can(me, "admin") && <WorkspacePolicy />}
      <Devices />
      <DangerZone />
    </>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <div className="alert success">
      <b>Save these recovery codes</b> somewhere safe. Each one signs you in once if you lose your phone. They will not
      be shown again.
      <pre style={{ background: "#fff", padding: 10, borderRadius: 8, columns: 2 }}>{codes.join("\n")}</pre>
      <div className="row">
        <button className="small" onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}>Copy</button>
        <button className="small" onClick={onDone}>I saved them</button>
      </div>
    </div>
  );
}

function TwoStepLogin() {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["me"] });

  const start = useMutation({ mutationFn: api.setup2fa, onSuccess: setSetup });
  const enable = useMutation({
    mutationFn: () => api.enable2fa(code),
    onSuccess: (r) => { setCodes(r.recovery_codes); setSetup(null); setCode(""); refresh(); },
  });
  const disable = useMutation({
    mutationFn: () => api.disable2fa(password, code),
    onSuccess: () => { setPassword(""); setCode(""); refresh(); },
  });
  const regenerate = useMutation({ mutationFn: () => api.newRecoveryCodes(code), onSuccess: (r) => { setCodes(r.recovery_codes); setCode(""); } });

  return (
    <div className="card">
      <div className="row">
        <h2 style={{ margin: 0 }}>Two-step login</h2>
        <span className={`badge ${me?.totp_enabled ? "approved" : "warn"}`}>{me?.totp_enabled ? "on" : "off"}</span>
      </div>
      <p className="muted">
        After your password, you also enter a 6-digit code from an authenticator app (Google Authenticator, Microsoft
        Authenticator, Authy…). This protects your account even if your password leaks.
      </p>
      <ErrorAlert error={start.error || enable.error || disable.error || regenerate.error} />
      {codes && <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />}

      {!me?.totp_enabled && !setup && (
        <button className="primary" disabled={start.isPending} onClick={() => start.mutate()}>Set up two-step login</button>
      )}
      {setup && (
        <div className="row" style={{ alignItems: "flex-start", gap: 20 }}>
          <img src={setup.qr_svg} alt="QR code for your authenticator app" style={{ width: 180, height: 180, background: "#fff", borderRadius: 8 }} />
          <div className="stack" style={{ flex: 1, minWidth: 240 }}>
            <div>1. Scan the QR code with your authenticator app.<br />
              <span className="muted small">Can't scan? Enter this key: <code style={{ wordBreak: "break-all" }}>{setup.secret}</code></span></div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>2. Enter the 6-digit code it shows</label>
              <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} style={{ maxWidth: 200 }} />
            </div>
            <div className="row">
              <button className="primary" disabled={code.trim().length < 6 || enable.isPending} onClick={() => enable.mutate()}>Turn on</button>
              <button onClick={() => setSetup(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
      {me?.totp_enabled && (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Current code</label>
            <input inputMode="numeric" style={{ width: 140 }} value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <button disabled={code.trim().length < 6 || regenerate.isPending} onClick={() => regenerate.mutate()}>New recovery codes</button>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Password (to turn off)</label>
            <input type="password" style={{ width: 180 }} value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <button className="danger" disabled={!password || code.trim().length < 6 || disable.isPending}
            onClick={() => confirm("Turn off two-step login?") && disable.mutate()}>Turn off</button>
        </div>
      )}
    </div>
  );
}

function WorkspacePolicy() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["org-settings"], queryFn: api.orgSettings });
  const save = useMutation({
    mutationFn: (require_2fa: boolean) => api.updateOrgSettings({ require_2fa }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["org-settings"] }),
  });
  if (!settings) return null;
  return (
    <div className="card">
      <h2>Workspace policies</h2>
      <ErrorAlert error={save.error} />
      <label className="checkbox">
        <input type="checkbox" checked={settings.require_2fa} onChange={(e) => save.mutate(e.target.checked)} />
        Require two-step login for everyone in this workspace
      </label>
      <p className="muted small" style={{ marginTop: 6 }}>Members without it are asked to set it up when they sign in.</p>
      <h3 style={{ marginTop: 16 }}>Approval workflow</h3>
      <select style={{ maxWidth: 420 }} value={settings.approval_steps}
        onChange={(e) => api.updateOrgSettings({ approval_steps: Number(e.target.value) }).then(() => queryClient.invalidateQueries({ queryKey: ["org-settings"] }))}>
        <option value={1}>One step: a reviewer approves</option>
        <option value={2}>Two steps: a reviewer approves, then an admin gives final approval</option>
      </select>
      <p className="muted small" style={{ marginTop: 6 }}>Only descriptions with final approval can be exported or pushed to a store.</p>
    </div>
  );
}

function describeAgent(ua: string | null): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return `${browser}${os ? ` on ${os}` : ""}`;
}

function Devices() {
  const queryClient = useQueryClient();
  const { data: sessions } = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["sessions"] });
  const revoke = useMutation({ mutationFn: api.revokeSession, onSuccess: refresh });
  const revokeOthers = useMutation({ mutationFn: api.revokeOtherSessions, onSuccess: refresh });
  return (
    <div className="card">
      <div className="row">
        <h2 style={{ margin: 0 }}>Signed-in devices</h2>
        <div className="spacer" />
        {(sessions?.length ?? 0) > 1 && (
          <button className="small danger" onClick={() => revokeOthers.mutate()}>Sign out all other devices</button>
        )}
      </div>
      <div className="table-wrap" style={{ marginTop: 10 }}>
        <table>
          <thead><tr><th>Device</th><th>Sign-in</th><th>IP</th><th>Last active</th><th /></tr></thead>
          <tbody>
            {sessions?.map((s) => (
              <tr key={s.id}>
                <td>{describeAgent(s.user_agent)} {s.current && <span className="badge approved">this device</span>}</td>
                <td className="small">{s.method} · {formatDate(s.created_at)}</td>
                <td className="small">{s.ip ?? "—"}</td>
                <td className="small">{formatDate(s.last_seen_at)}</td>
                <td style={{ textAlign: "right" }}>{!s.current && <button className="small" onClick={() => revoke.mutate(s.id)}>Sign out</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DangerZone() {
  const navigate = useNavigate();
  const { data: me } = useMe();
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const download = useMutation({ mutationFn: () => api.downloadFile("/settings/export-data", "workspace-export.zip") });
  const leave = () => { auth.set(null); navigate("/login"); };
  const deleteAccount = useMutation({ mutationFn: () => api.deleteMyAccount(password), onSuccess: leave });
  const deleteOrg = useMutation({ mutationFn: () => api.deleteOrganization(orgName, password), onSuccess: leave });
  if (!me) return null;
  const owner = me.role === "owner";

  return (
    <div className="card" style={{ borderColor: "#fecaca" }}>
      <h2>Your data</h2>
      <ErrorAlert error={download.error || deleteAccount.error || deleteOrg.error} />
      {owner && (
        <div className="row" style={{ marginBottom: 14 }}>
          <span>Download everything stored for this workspace (ZIP of JSON files).</span>
          <div className="spacer" />
          <button disabled={download.isPending} onClick={() => download.mutate()}>{download.isPending ? "Preparing…" : "Download data"}</button>
        </div>
      )}
      <h3 style={{ color: "var(--danger)" }}>Danger zone</h3>
      {owner ? (
        <>
          <p className="muted small">
            Permanently delete <b>{me.organization.name}</b>: all catalogs, descriptions, team members, connections and
            history. This cannot be undone. Type the workspace name and your password to confirm.
          </p>
          <div className="row">
            <input style={{ maxWidth: 240 }} placeholder={me.organization.name} value={orgName} onChange={(e) => setOrgName(e.target.value)} />
            <input type="password" style={{ maxWidth: 200 }} placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="danger" disabled={orgName.trim() !== me.organization.name.trim() || !password || deleteOrg.isPending}
              onClick={() => confirm("Delete the whole workspace permanently?") && deleteOrg.mutate()}>Delete workspace</button>
          </div>
        </>
      ) : (
        <>
          <p className="muted small">Delete your account. Descriptions you worked on stay with the company; your personal data is removed.</p>
          <div className="row">
            <input type="password" style={{ maxWidth: 220 }} placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="danger" disabled={!password || deleteAccount.isPending}
              onClick={() => confirm("Delete your account permanently?") && deleteAccount.mutate()}>Delete my account</button>
          </div>
        </>
      )}
    </div>
  );
}
