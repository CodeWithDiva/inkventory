import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, AdminOrg } from "../api";
import { ErrorAlert, formatDate, Loading, useMe } from "../components";

export default function AdminPage() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["admin-orgs"], queryFn: api.adminOrgs, enabled: !!me?.is_superadmin });
  const [search, setSearch] = useState("");
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.adminUpdateOrg>[1] }) => api.adminUpdateOrg(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orgs"] }),
  });

  if (!me?.is_superadmin) return <div className="alert warning">Not available.</div>;
  if (isLoading) return <Loading />;
  const orgs = (data ?? []).filter((o) =>
    `${o.name} ${o.owner_email ?? ""}`.toLowerCase().includes(search.toLowerCase()),
  );
  const pending = (data ?? []).filter((o) => o.requested_plan).length;
  const totals = (data ?? []).reduce(
    (t, o) => ({ usage: t.usage + o.usage_this_month, paid: t.paid + (o.plan !== "free" ? 1 : 0) }),
    { usage: 0, paid: 0 },
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Platform admin</h1>
          <p className="muted">All customer organizations. Change plans here for bank-transfer / invoice customers.</p>
        </div>
      </div>
      <ErrorAlert error={error || update.error} />
      <div className="grid grid-4">
        <div className="card"><div className="stat-label">Organizations</div><div className="stat-value">{data?.length ?? 0}</div></div>
        <div className="card"><div className="stat-label">Paying</div><div className="stat-value">{totals.paid}</div></div>
        <div className="card"><div className="stat-label">Descriptions this month</div><div className="stat-value">{totals.usage}</div></div>
        <div className="card"><div className="stat-label">Upgrade requests</div><div className="stat-value">{pending}</div></div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <input style={{ maxWidth: 320, marginBottom: 12 }} placeholder="Search name or owner email…" value={search}
          onChange={(e) => setSearch(e.target.value)} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Organization</th><th>Plan</th><th>Status</th><th>Usage / limit</th><th>Products</th><th>Team</th><th>Own key</th><th>Joined</th></tr>
            </thead>
            <tbody>
              {orgs.map((o) => <OrgRow key={o.id} org={o} onUpdate={(body) => update.mutate({ id: o.id, body })} />)}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function OrgRow({ org, onUpdate }: { org: AdminOrg; onUpdate: (body: Parameters<typeof api.adminUpdateOrg>[1]) => void }) {
  const [limit, setLimit] = useState(String(org.monthly_generation_limit));
  // A plan change resets the limit on the server; show the new value.
  useEffect(() => setLimit(String(org.monthly_generation_limit)), [org.monthly_generation_limit]);
  return (
    <tr>
      <td>
        <b>{org.name}</b><div className="muted small">{org.owner_email}</div>
        {org.requested_plan && (
          <span className="badge warn" title={`Requested ${formatDate(org.requested_at)}`}>Requested: {org.requested_plan}</span>
        )}
      </td>
      <td>
        <select style={{ width: 120 }} value={org.plan}
          onChange={(e) => confirm(`Change ${org.name} to ${e.target.value}?`) && onUpdate({ plan: e.target.value })}>
          <option value="free">free</option>
          <option value="pro">pro</option>
          <option value="business">business</option>
        </select>
        {org.billing_provider && <div className="muted small">{org.billing_provider}</div>}
      </td>
      <td>
        <select style={{ width: 120 }} value={org.plan_status} onChange={(e) => onUpdate({ plan_status: e.target.value })}>
          <option value="active">active</option>
          <option value="past_due">past_due</option>
          <option value="cancelled">cancelled</option>
        </select>
      </td>
      <td>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <span className="small">{org.usage_this_month} /</span>
          <input type="number" style={{ width: 90 }} value={limit} onChange={(e) => setLimit(e.target.value)} />
          {Number(limit) !== org.monthly_generation_limit && (
            <button className="small" onClick={() => onUpdate({ monthly_generation_limit: Number(limit) })}>Save</button>
          )}
        </div>
      </td>
      <td>{org.products}</td>
      <td>{org.members}</td>
      <td>{org.has_own_anthropic_key ? "yes" : "—"}</td>
      <td className="small">{formatDate(org.created_at)}</td>
    </tr>
  );
}
