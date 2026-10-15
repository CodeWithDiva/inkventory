import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { can, Empty, ErrorAlert, formatDate, Loading, SettingsTabs, useMe } from "../components";

export default function AuditLogPage() {
  const { data: me } = useMe();
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const actions = useQuery({ queryKey: ["audit-actions"], queryFn: api.auditActions, enabled: can(me, "admin") });
  const { data, isLoading, error } = useQuery({
    queryKey: ["audit", action, page],
    queryFn: () => api.auditLog({ action, page }),
    enabled: can(me, "admin"),
    placeholderData: (prev) => prev,
  });
  const download = useMutation({ mutationFn: () => api.downloadFile("/settings/audit-log/export", "audit-log.csv") });

  if (!can(me, "admin")) return <div className="alert warning">Only admins can view the audit log.</div>;
  const groups = [...new Set(Object.keys(actions.data ?? {}).map((a) => a.split(".")[0]))];

  return (
    <>
      <SettingsTabs />
      <div className="page-head">
        <div>
          <h1>Audit log</h1>
          <p className="muted">Who did what in this workspace, when and from where.</p>
        </div>
        <button disabled={download.isPending} onClick={() => download.mutate()}>Export CSV</button>
      </div>
      <ErrorAlert error={error || download.error} />
      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <select style={{ width: 220 }} value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}>
            <option value="">All activity</option>
            {groups.map((g) => <option key={g} value={g}>{g}</option>)}
            {Object.entries(actions.data ?? {}).map(([a, label]) => <option key={a} value={a}>— {label}</option>)}
          </select>
        </div>
        {isLoading ? <Loading /> : !data?.length ? <Empty>No activity recorded.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Time</th><th>Who</th><th>What</th><th>Details</th><th>IP</th></tr></thead>
              <tbody>
                {data.map((e) => (
                  <tr key={e.id}>
                    <td className="small" style={{ whiteSpace: "nowrap" }}>{formatDate(e.created_at)}</td>
                    <td className="small">{e.actor}</td>
                    <td>
                      {actions.data?.[e.action] ?? e.action}
                      <div className="muted small">{e.action}{e.target_type ? ` · ${e.target_type} #${e.target_id}` : ""}</div>
                    </td>
                    <td className="small" style={{ maxWidth: 380, wordBreak: "break-word" }}>
                      {Object.entries(e.details).map(([k, v]) => (
                        <span key={k} className="chip">{k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
                      ))}
                    </td>
                    <td className="small">{e.ip ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="row" style={{ marginTop: 12 }}>
          <div className="spacer" />
          <button className="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Newer</button>
          <span className="small">Page {page}</span>
          <button className="small" disabled={(data?.length ?? 0) < 50} onClick={() => setPage(page + 1)}>Older</button>
        </div>
      </div>
    </>
  );
}
