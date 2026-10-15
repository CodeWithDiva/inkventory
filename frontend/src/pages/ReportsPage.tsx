import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, Report } from "../api";
import { Empty, ErrorAlert, Loading, money, useOptions } from "../components";

// Categorical slots in fixed order (validated reference palette); text never uses these colors.
const SERIES = [
  { key: "generated", label: "Generated", color: "var(--series-1)" },
  { key: "approved", label: "Approved", color: "var(--series-2)" },
  { key: "rejected", label: "Rejected", color: "var(--series-3)" },
] as const;

const WARNING_LABELS: Record<string, string> = {
  unverified_facts: "Unverified numbers",
  duplicate_content: "Duplicate content",
  wrong_language: "Wrong language",
  channel_rules: "Marketplace rules",
  glossary: "Glossary words",
  glossary_fixed: "Glossary auto-fixes",
  note: "Notes",
};

export default function ReportsPage() {
  const [days, setDays] = useState(30);
  const [catalogId, setCatalogId] = useState<number | null>(null);
  const { data: catalogs } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs });
  const { data, isLoading, error } = useQuery({
    queryKey: ["report", days, catalogId],
    queryFn: () => api.report(days, catalogId),
    placeholderData: (prev) => prev,
  });
  const download = useMutation({
    mutationFn: () => api.downloadFile(`/reports/daily.csv?days=${days}${catalogId ? `&catalog_id=${catalogId}` : ""}`, "report.csv"),
  });

  return (
    <div className="viz-root">
      <div className="page-head">
        <div>
          <h1>Insights</h1>
          <p className="muted">Progress, quality and cost over time.</p>
        </div>
        <button disabled={download.isPending} onClick={() => download.mutate()}>Export CSV</button>
      </div>
      <div className="row" style={{ marginBottom: 16 }}>
        <select style={{ width: 160 }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
          <option value={365}>Last 12 months</option>
        </select>
        <select style={{ width: 240 }} value={catalogId ?? ""} onChange={(e) => setCatalogId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">All catalogs</option>
          {catalogs?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <ErrorAlert error={error || download.error} />
      {isLoading || !data ? <Loading /> : <ReportBody data={data} />}
    </div>
  );
}

function ReportBody({ data }: { data: Report }) {
  const { data: options } = useOptions();
  const t = data.totals;
  const empty = t.generated + t.approved + t.rejected + t.edited === 0;
  return (
    <>
      <div className="grid grid-4">
        <Tile label="Generated" value={t.generated} />
        <Tile label="Approved" value={t.approved} />
        <Tile label="Edited by hand" value={t.edited} />
        <Tile label="Rejected" value={t.rejected} />
        <Tile label="Avg SEO score" value={t.avg_seo ?? "—"} />
        <Tile label="AI cost" value={money(t.cost_usd)} />
      </div>

      {empty ? (
        <div className="card" style={{ marginTop: 16 }}><Empty>No activity in this period yet.</Empty></div>
      ) : (
        <>
          <div className="card" style={{ marginTop: 16 }}>
            <h2>Daily activity</h2>
            <LineChart series={data.series} lines={SERIES.map((s) => ({ ...s, values: data.series.map((d) => d[s.key]) }))} />
          </div>
          <div className="grid grid-2" style={{ marginTop: 16 }}>
            <div className="card" style={{ marginTop: 0 }}>
              <h2>Average SEO score of new descriptions</h2>
              <LineChart series={data.series} max={100} width={480}
                lines={[{ key: "avg_seo", label: "Avg SEO", color: "var(--series-1)", values: data.series.map((d) => d.avg_seo) }]} />
            </div>
            <div className="card" style={{ marginTop: 0 }}>
              <h2>SEO score of current descriptions</h2>
              <BarList rows={Object.entries(data.seo_distribution).map(([k, v]) => ({ label: `Score ${k}`, value: v }))} />
              <h3 style={{ marginTop: 18 }}>Quality flags on current descriptions</h3>
              {Object.keys(data.warnings).length === 0 ? <p className="muted small">No flags.</p> : (
                <BarList rows={Object.entries(data.warnings).map(([k, v]) => ({ label: WARNING_LABELS[k] ?? k, value: v }))} />
              )}
            </div>
          </div>
        </>
      )}

      <div className="grid grid-2" style={{ marginTop: 16 }}>
        <div className="card" style={{ marginTop: 0 }}>
          <h2>Team</h2>
          {data.team.length === 0 ? <p className="muted small">No team activity in this period.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Member</th><th>Generated</th><th>Edited</th><th>Approved</th><th>Rejected</th><th>Comments</th></tr></thead>
                <tbody>
                  {data.team.map((m) => (
                    <tr key={m.user_id}>
                      <td>{m.name}<div className="muted small">{m.email}</div></td>
                      <td>{m.generated}</td><td>{m.edited}</td><td>{m.approved}</td><td>{m.rejected}</td><td>{m.comments}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="card" style={{ marginTop: 0 }}>
          <h2>AI cost by catalog</h2>
          {data.cost_by_catalog.length === 0 ? <p className="muted small">No generation in this period.</p> : (
            <BarList rows={data.cost_by_catalog.map((c) => ({ label: c.catalog, value: c.cost_usd, display: `${money(c.cost_usd)} · ${c.generated} items` }))} />
          )}
          <h3 style={{ marginTop: 18 }}>Status by language</h3>
          <table>
            <thead><tr><th>Language</th><th>Draft</th><th>In review</th><th>Approved</th><th>Rejected</th></tr></thead>
            <tbody>
              {Object.entries(data.languages).map(([lang, s]) => (
                <tr key={lang}>
                  <td>{options?.languages[lang] ?? lang}</td><td>{s.draft}</td><td>{s.in_review}</td><td>{s.approved}</td><td>{s.rejected}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function Tile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card" style={{ marginTop: 0 }}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

type Line = { key: string; label: string; color: string; values: (number | null)[] };

function LineChart({ series, lines, max, width = 1040 }: { series: { date: string }[]; lines: Line[]; max?: number; width?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = width, H = 220, L = 36, R = 12, T = 12, B = 26;
  const n = series.length;
  const top = max ?? Math.max(1, ...lines.flatMap((l) => l.values.map((v) => v ?? 0)));
  const niceTop = max ?? (Math.ceil(top / 5) * 5 || 5);
  const x = (i: number) => L + (n <= 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
  const y = (v: number) => T + (H - T - B) * (1 - v / niceTop);
  const ticks = [0, niceTop / 2, niceTop];
  const labelEvery = Math.max(1, Math.ceil(n / 8));
  const fmt = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

  const path = (values: (number | null)[]) => {
    let d = "";
    values.forEach((v, i) => {
      if (v === null) return;
      d += `${d && values[i - 1] !== null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    });
    return d;
  };
  // A single series is drawn as an area: each unbroken run of values gets a soft fill down to zero.
  const area = (values: (number | null)[]) => {
    const runs: number[][] = [];
    values.forEach((v, i) => {
      if (v === null) return;
      if (i > 0 && values[i - 1] !== null) runs[runs.length - 1].push(i);
      else runs.push([i]);
    });
    return runs.filter((r) => r.length > 1).map((r) =>
      `M${x(r[0]).toFixed(1)},${y(0).toFixed(1)}` + r.map((i) => `L${x(i).toFixed(1)},${y(values[i] as number).toFixed(1)}`).join("") +
      `L${x(r[r.length - 1]).toFixed(1)},${y(0).toFixed(1)}Z`).join("");
  };
  const gradientId = `area-${lines.map((l) => l.key).join("-")}`;
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = n <= 1 ? 0 : Math.round(((px - L) / (W - L - R)) * (n - 1));
    setHover(Math.min(n - 1, Math.max(0, i)));
  };

  return (
    <div style={{ position: "relative" }}>
      {lines.length > 1 && (
        <div className="row small" style={{ marginBottom: 6, gap: 14 }}>
          {lines.map((l) => (
            <span key={l.key} className="row" style={{ gap: 6 }}>
              <span style={{ width: 14, height: 3, borderRadius: 2, background: l.color, display: "inline-block" }} />{l.label}
            </span>
          ))}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={lines.map((l) => l.label).join(", ") + " per day"}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)} style={{ display: "block" }}>
        {ticks.map((tv) => (
          <g key={tv}>
            <line x1={L} x2={W - R} y1={y(tv)} y2={y(tv)} stroke="var(--grid)" strokeWidth={1} />
            <text x={L - 6} y={y(tv) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">{Math.round(tv)}</text>
          </g>
        ))}
        {series.map((s, i) => i % labelEvery === 0 && (
          <text key={s.date} x={x(i)} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--muted)">{fmt(s.date)}</text>
        ))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="var(--muted)" strokeWidth={1} strokeDasharray="3 3" />}
        {lines.length === 1 && (
          <>
            <defs>
              <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={lines[0].color} stopOpacity={0.22} />
                <stop offset="100%" stopColor={lines[0].color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <path d={area(lines[0].values)} fill={`url(#${gradientId})`} />
          </>
        )}
        {lines.map((l) => <path key={l.key} d={path(l.values)} fill="none" stroke={l.color} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />)}
        {/* Points with no neighbour would be invisible as a line: mark them. */}
        {lines.map((l) => l.values.map((v, i) => v !== null && (l.values[i - 1] ?? null) === null && (l.values[i + 1] ?? null) === null && (
          <circle key={`${l.key}-${i}`} cx={x(i)} cy={y(v)} r={4} fill={l.color} stroke="var(--surface)" strokeWidth={2} />
        )))}
        {hover !== null && lines.map((l) => l.values[hover] !== null && (
          <circle key={l.key} cx={x(hover)} cy={y(l.values[hover] as number)} r={4.5} fill={l.color} stroke="var(--surface)" strokeWidth={2} />
        ))}
      </svg>
      {hover !== null && (
        <div className="chart-tip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <div style={{ fontWeight: 600 }}>{fmt(series[hover].date)}</div>
          {lines.map((l) => (
            <div key={l.key} className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
              <span style={{ width: 8, height: 8, borderRadius: 4, background: l.color, display: "inline-block" }} />
              <span>{l.label}</span><span className="spacer" /><b>{l.values[hover] ?? "—"}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BarList({ rows }: { rows: { label: string; value: number; display?: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="stack" style={{ gap: 8 }}>
      {rows.map((r) => (
        <div key={r.label} title={`${r.label}: ${r.display ?? r.value}`}>
          <div className="row small" style={{ marginBottom: 3 }}>
            <span>{r.label}</span><span className="spacer" /><b>{r.display ?? r.value}</b>
          </div>
          <div style={{ height: 8, background: "var(--surface-2)", borderRadius: 4 }}>
            <div style={{ width: `${(r.value / max) * 100}%`, height: "100%", background: "var(--series-1)", borderRadius: 4, minWidth: r.value ? 4 : 0 }} />
          </div>
        </div>
      ))}
    </div>
  );
}
