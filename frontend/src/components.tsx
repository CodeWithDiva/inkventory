import { ReactNode, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink } from "react-router-dom";
import { api, Engine, Me, Options, Role } from "./api";
import { Icon, IconName } from "./icons";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: api.me });
}

export function useOptions() {
  return useQuery({ queryKey: ["options"], queryFn: api.options, staleTime: Infinity });
}

const PERMISSIONS: Record<string, Role[]> = {
  edit: ["owner", "admin", "editor"],
  review: ["owner", "admin", "reviewer"],
  admin: ["owner", "admin"],
};

export function can(me: Me | undefined, action: keyof typeof PERMISSIONS | string): boolean {
  return !!me && (PERMISSIONS[action] ?? []).includes(me.role);
}

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft", in_review: "In review", approved: "Approved", rejected: "Rejected",
  pending: "Queued", running: "Running", completed: "Completed", failed: "Failed", cancelled: "Cancelled",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge dot ${status}`}>{STATUS_LABELS[status] ?? status}</span>;
}

export function SeoScore({ score }: { score: number }) {
  const cls = score >= 80 ? "good" : score >= 60 ? "ok" : "bad";
  return <span className={`score ${cls}`}>{score}</span>;
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <div className="alert error" role="alert"><Icon name="alert" size={16} /><span>{message}</span></div>;
}

export function Loading() {
  return (
    <div className="empty" aria-busy="true">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ animation: "spin 0.8s linear infinite" }}>
        <path d="M21 12a9 9 0 1 1-6.22-8.56" strokeLinecap="round" />
      </svg>
    </div>
  );
}

export function Empty({ children, icon = "file", title }: { children: ReactNode; icon?: IconName; title?: string }) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon name={icon} size={22} /></div>
      {title && <div className="empty-title">{title}</div>}
      <div>{children}</div>
    </div>
  );
}

export function ProgressBar({ value, total }: { value: number; total: number }) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className="progress" title={`${pct}%`}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

export function formatDate(value: string | null | undefined) {
  return value ? parseUtc(value).toLocaleString() : "—";
}

/** Default engine if usable, else the first usable non-demo engine, else demo. */
export function pickDefaultEngine(options: Options | undefined): string | undefined {
  if (!options) return undefined;
  const usable = options.engines.filter((e) => e.available);
  return (
    usable.find((e) => e.id === options.default_engine)?.id ??
    usable.find((e) => e.kind !== "demo")?.id ??
    usable[0]?.id
  );
}

export function EngineSelect({ value, onChange }: { value: string | undefined; onChange: (id: string) => void }) {
  const { data: options } = useOptions();
  const selected = options?.engines.find((e) => e.id === value);
  const groups: [Engine["kind"], string][] = [
    ["free", "Free cloud AI"],
    ["local", "Free – runs on the server"],
    ["cloud", "Claude / paid AI"],
    ["demo", "Demo"],
  ];
  return (
    <>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {groups.map(([kind, label]) => {
          const list = options?.engines.filter((e) => e.kind === kind) ?? [];
          return list.length ? (
            <optgroup key={kind} label={label}>
              {list.map((e) => (
                <option key={e.id} value={e.id} disabled={!e.available}>
                  {e.label}{e.available ? "" : " (not available)"}
                </option>
              ))}
            </optgroup>
          ) : null;
        })}
      </select>
      {selected && <p className="muted small" style={{ marginTop: 4 }}>{selected.note}</p>}
      {options?.engines.filter((e) => !e.available).map((e) => (
        <p key={e.id} className="muted small" style={{ margin: 0 }}>
          {e.label}: {e.note}{" "}
          {e.needs_upgrade && <Link to="/billing">Upgrade →</Link>}
          {e.id === "anthropic" && !e.needs_upgrade && <Link to="/settings">Settings →</Link>}
        </p>
      ))}
    </>
  );
}

export function minutes(value: number) {
  if (value < 1) return "under a minute";
  if (value < 90) return `~${Math.round(value)} min`;
  return `~${(value / 60).toFixed(1)} hours`;
}

export function money(value: number) {
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

/** API timestamps are UTC; older rows may lack the zone suffix, so treat those as UTC too. */
function parseUtc(value: string) {
  return new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(value) ? value : value + "Z");
}

export function timeAgo(value: string) {
  const s = Math.max(0, (Date.now() - parseUtc(value).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} hour${s < 7200 ? "" : "s"} ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} day${s < 172800 ? "" : "s"} ago`;
  return parseUtc(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** Product photo: external URLs load directly; uploaded photos need the auth header, so they load as blobs. */
export function ProductThumb({ productId, index = 0, src, size = 40, className = "" }: {
  productId: number; index?: number; src?: string | null; size?: number | "fill"; className?: string;
}) {
  const [url, setUrl] = useState<string | null>(src && src.startsWith("http") ? src : null);
  const [failed, setFailed] = useState(false);
  const needsBlob = src !== undefined && !(src && src.startsWith("http"));
  useEffect(() => {
    if (!needsBlob) return;
    let objectUrl = "";
    let cancelled = false;
    api.imageObjectUrl(productId, index).then((u) => {
      objectUrl = u;
      if (!cancelled) setUrl(u);
    }).catch(() => setFailed(true));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [needsBlob, productId, index]);
  const style = size === "fill" ? undefined : { width: size, height: size };
  return (
    <span className={`thumb ${className}`} style={style}>
      {url && !failed ? <img src={url} alt="" onError={() => setFailed(true)} /> : <Icon name="image" size={size === "fill" ? 22 : 16} />}
    </span>
  );
}

export function SeoRing({ score, size = 112 }: { score: number | null; size?: number }) {
  const r = size / 2 - 7;
  const c = 2 * Math.PI * r;
  const value = score ?? 0;
  const tone = value >= 80 ? "var(--success-500)" : value >= 60 ? "var(--warning-500)" : "var(--danger-500)";
  return (
    <div className="seo-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={7} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth={7} strokeLinecap="round"
          strokeDasharray={`${(c * value) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </svg>
      <div className="seo-ring-label">
        <strong>{score ?? "—"}</strong>
        <span>/100</span>
      </div>
    </div>
  );
}

/** Tabs that group the workspace settings pages, as in a settings area of a SaaS product. */
export function SettingsTabs() {
  const { data: me } = useMe();
  const tabs: { to: string; label: string; show?: boolean }[] = [
    { to: "/settings", label: "Organization" },
    { to: "/team", label: "Team" },
    { to: "/billing", label: "Billing" },
    { to: "/integrations", label: "Integrations" },
    { to: "/developer", label: "API keys", show: can(me, "admin") },
    { to: "/security", label: "Security" },
    { to: "/audit-log", label: "Audit log", show: can(me, "admin") },
  ];
  return (
    <nav className="tabs" aria-label="Settings sections">
      {tabs.filter((t) => t.show !== false).map((t) => (
        <NavLink key={t.to} to={t.to} className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>{t.label}</NavLink>
      ))}
    </nav>
  );
}
