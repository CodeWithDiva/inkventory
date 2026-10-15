import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Plan } from "../api";
import { can, ErrorAlert, formatDate, Loading, ProgressBar, SettingsTabs, useMe } from "../components";

export default function BillingPage() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["billing"], queryFn: api.billing });
  const [requesting, setRequesting] = useState<Plan | null>(null);
  const [message, setMessage] = useState("");
  const [done, setDone] = useState<string | null>(null);

  const checkout = useMutation({
    mutationFn: api.checkout,
    onSuccess: ({ url }) => { window.location.href = url; },
  });
  const request = useMutation({
    mutationFn: () => api.requestUpgrade(requesting!.id, message),
    onSuccess: (r) => {
      setDone(r.message);
      setRequesting(null);
      setMessage("");
      queryClient.invalidateQueries({ queryKey: ["billing"] });
    },
  });

  if (isLoading) return <Loading />;
  if (!data) return <ErrorAlert error={error} />;
  const admin = can(me, "admin");
  const requestedName = data.plans.find((p) => p.id === data.requested_plan)?.name;

  return (
    <>
      <SettingsTabs />
      <div className="page-head">
        <div>
          <h1>Plan &amp; billing</h1>
          <p className="muted">Your current plan, this month's usage and upgrades.</p>
        </div>
      </div>
      <ErrorAlert error={checkout.error || request.error} />
      {me?.is_superadmin && (
        <div className="alert info">
          You are the platform owner. Change any organization's plan, including your own, in{" "}
          <Link to="/admin">Platform admin</Link>.
        </div>
      )}
      {done && <div className="alert success">{done}</div>}
      {!done && requestedName && (
        <div className="alert info">
          Upgrade to <b>{requestedName}</b> requested on {formatDate(data.requested_at)}. We'll contact you with payment
          details.
        </div>
      )}

      <div className="grid grid-4">
        <div className="card">
          <div className="stat-label">Current plan</div>
          <div className="stat-value">{data.plan.name}</div>
          <div className="row small" style={{ marginTop: 6 }}>
            <span className={`badge ${data.plan_status === "active" ? "approved" : "warn"}`}>{data.plan_status}</span>
            {data.plan_renews_at && <span className="muted">renews/ends {formatDate(data.plan_renews_at)}</span>}
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Descriptions this month</div>
          <div className="stat-value">{data.usage_this_month} <span className="muted small">/ {data.monthly_limit}</span></div>
          <ProgressBar value={data.usage_this_month} total={data.monthly_limit} />
        </div>
        <div className="card">
          <div className="stat-label">Team members</div>
          <div className="stat-value">{data.team_members} <span className="muted small">/ {data.plan.max_users}</span></div>
        </div>
      </div>

      {requesting && (
        <div className="card" style={{ marginTop: 16, borderColor: "var(--primary)" }}>
          <h2>Request the {requesting.name} plan ({requesting.price_label})</h2>
          <p className="muted">
            We'll email you payment details (bank transfer or invoice) and activate the plan once payment is received.
          </p>
          <div className="field">
            <label>Message (optional)</label>
            <textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)}
              placeholder="e.g. Please send an invoice for yearly billing to accounts@ourcompany.com" />
          </div>
          <div className="row">
            <button className="primary" disabled={request.isPending} onClick={() => request.mutate()}>
              {request.isPending ? "Sending…" : "Send request"}
            </button>
            <button onClick={() => setRequesting(null)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="grid grid-4" style={{ marginTop: 16 }}>
        {data.plans.map((p) => {
          const current = p.id === data.plan.id;
          return (
            <div className="card" key={p.id} style={current ? { borderColor: "var(--primary)", borderWidth: 2 } : undefined}>
              <h2 style={{ marginBottom: 2 }}>{p.name}</h2>
              <div className="stat-value" style={{ fontSize: 22 }}>{p.price_label}</div>
              <ul className="small" style={{ paddingLeft: 18 }}>
                {p.features.map((f) => <li key={f}>{f}</li>)}
              </ul>
              {current ? (
                <span className="badge approved">Your plan</span>
              ) : p.id === "free" ? null : !admin ? (
                <span className="muted small">Ask your workspace admin to upgrade</span>
              ) : p.can_checkout ? (
                <button className="primary" disabled={checkout.isPending} onClick={() => checkout.mutate(p.id)}>
                  Upgrade to {p.name}
                </button>
              ) : data.requested_plan === p.id ? (
                <span className="badge warn">Requested</span>
              ) : (
                <button className="primary" onClick={() => { setDone(null); setRequesting(p); }}>
                  Request {p.name}
                </button>
              )}
            </div>
          );
        })}
      </div>
      <p className="muted small" style={{ marginTop: 12 }}>
        Prefer bank transfer or an invoice? Use "Request" on a plan
        {data.billing_contact ? ` or contact ${data.billing_contact}` : ""}, and we'll activate it manually.
      </p>
    </>
  );
}
