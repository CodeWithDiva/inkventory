import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, CatalogCard, Dashboard } from "../api";
import { ErrorAlert, Loading, ProgressBar, StatusBadge, timeAgo, useMe } from "../components";
import { Icon, IconName } from "../icons";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const WORKFLOW: { to: string; icon: IconName; title: string; text: string }[] = [
  { to: "/catalogs/upload", icon: "upload", title: "Upload products", text: "CSV, Google Sheets, API or Shopify / WooCommerce" },
  { to: "/generate", icon: "sparkle", title: "AI generation", text: "Unique descriptions in your brand voice" },
  { to: "/review", icon: "checkCircle", title: "Review & approve", text: "Edit, compare versions and sign off" },
  { to: "/export", icon: "send", title: "Export or publish", text: "Marketplace CSVs or push to your store" },
];

type Activity = { key: string; icon: IconName; title: string; when: string; status: string; link: string };

export default function DashboardPage() {
  const { data: me } = useMe();
  const { data, isLoading, error } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 10000 });
  const { data: catalogs } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs });

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorAlert error={error} />;

  const d = data.descriptions;
  const toReview = d.draft + (d.in_review ?? 0);
  const catalogName = (id: number) => catalogs?.find((c) => c.id === id)?.name ?? `catalog #${id}`;

  const activity: Activity[] = [
    ...data.recent_jobs.map((job) => ({
      key: `job-${job.id}`,
      icon: (job.status === "completed" ? "checkCircle" : "sparkle") as IconName,
      title: job.status === "running" || job.status === "pending"
        ? `Generating descriptions for "${catalogName(job.catalog_id)}" (${job.completed}/${job.total})`
        : `Descriptions generated for "${catalogName(job.catalog_id)}" (${job.completed} of ${job.total})`,
      when: job.created_at,
      status: job.status,
      link: `/catalogs/${job.catalog_id}`,
    })),
    ...(catalogs ?? []).slice(0, 5).map((c) => ({
      key: `cat-${c.id}`,
      icon: "upload" as IconName,
      title: `Catalog "${c.name}" uploaded · ${c.product_count} products`,
      when: c.created_at,
      status: "completed",
      link: `/catalogs/${c.id}`,
    })),
  ].sort((a, b) => b.when.localeCompare(a.when)).slice(0, 5);

  return (
    <>
      <section className="hero">
        <div>
          <h1>{greeting()}, {me?.full_name.split(" ")[0]}</h1>
          <p>Turn your product data into unique, SEO-optimized descriptions that sell, in minutes instead of weeks.</p>
          <div className="row">
            <Link to="/catalogs/upload" className="btn primary"><Icon name="plus" size={16} />Upload products</Link>
            {data.products > 0 && <Link to="/generate" className="btn"><Icon name="sparkle" size={16} />Generate</Link>}
          </div>
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="hero-doc-back" />
          <div className="hero-doc"><i /><i /><i /><i /><i /></div>
          <div className="hero-checks">
            <span><Icon name="checkCircle" size={16} />Unique content</span>
            <span><Icon name="checkCircle" size={16} />SEO optimized</span>
            <span><Icon name="checkCircle" size={16} />15 languages</span>
          </div>
        </div>
      </section>

      <div className="workflow">
        {WORKFLOW.map((w) => (
          <Link key={w.to} to={w.to} className="workflow-card">
            <span className="workflow-icon"><Icon name={w.icon} size={20} /></span>
            <span>
              <strong>{w.title}</strong>
              <span className="muted">{w.text}</span>
            </span>
            <Icon name="arrowRight" size={16} className="go" />
          </Link>
        ))}
      </div>

      <div className="grid grid-4 stretch" style={{ marginBottom: 20 }}>
        <Stat label="Products" value={data.products} icon="box" hint={`${data.catalogs} catalog${data.catalogs === 1 ? "" : "s"}`} />
        <Stat label="Waiting for review" value={toReview} icon="clock"
          hint={d.in_review ? `${d.in_review} need final approval` : "Drafts written by AI"} link="/review" />
        <Stat label="Approved" value={d.approved} icon="checkCircle" hint={`${d.rejected} rejected`} />
        <Stat label="This month" value={data.usage.this_month} icon="layers"
          hint={`of ${data.usage.limit.toLocaleString()} descriptions`} meter={[data.usage.this_month, data.usage.limit]} />
      </div>

      <div className="dash-row">
        <NeedsAttention items={data.attention} />
        <QualityCard quality={data.quality} trend={data.trend} />
      </div>

      <div className="dash-bottom three">
        <CatalogProgress catalogs={catalogs ?? []} />
        <div className="card">
          <div className="card-head">
            <h2>Recent activity</h2>
            <Link to="/catalogs" className="small">View all</Link>
          </div>
          {activity.length === 0 ? (
            <p className="muted">Nothing here yet. Upload a catalog to get started.</p>
          ) : (
            <ul className="activity">
              {activity.map((a) => (
                <li key={a.key}>
                  <span className={`activity-icon ${a.status === "completed" ? "ok" : ""}`}><Icon name={a.icon} size={16} /></span>
                  <Link to={a.link} style={{ color: "inherit", minWidth: 0 }}>
                    <div className="activity-title">{a.title}</div>
                    <div className="muted small">{timeAgo(a.when)}</div>
                  </Link>
                  <StatusBadge status={a.status} />
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="promo">
          <div className="promo-glow" />
          <div style={{ position: "relative" }}>
            <h2>Better descriptions.<br />Higher sales.</h2>
            <p>Every description is checked for SEO, accuracy and marketplace rules before your team approves it.</p>
          </div>
          <Link to={data.products ? "/generate" : "/catalogs/upload"} className="btn">
            {data.products ? "Generate now" : "Get started"}
          </Link>
          {data.avg_seo_score != null && (
            <div className="promo-metric">
              <strong>{data.avg_seo_score}</strong>
              <span>average SEO score</span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Stat({ label, value, icon, hint, link, meter }: {
  label: string; value: number; icon: IconName; hint?: string; link?: string; meter?: [number, number];
}) {
  const body = (
    <div className="card" style={{ height: "100%" }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
        <div className="stat-label">{label}</div>
        <span className="stat-icon"><Icon name={icon} size={18} /></span>
      </div>
      <div className="stat-value">{value.toLocaleString()}</div>
      {meter && <div style={{ margin: "6px 0" }}><ProgressBar value={meter[0]} total={meter[1]} /></div>}
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
  return link ? <Link to={link} style={{ color: "inherit" }}>{body}</Link> : body;
}

const FLAG_LABELS: Record<string, string> = {
  unverified_facts: "Facts not in product data",
  wrong_language: "Wrong language",
  duplicate_content: "Too similar to another product",
  channel_rules: "Marketplace rules not met",
  glossary: "Uses glossary avoid-words",
  ai_style: "Reads machine-written",
  photo_mismatch: "Photo may not match product",
  note: "Notes from the AI engine",
};

function NeedsAttention({ items }: { items: Dashboard["attention"] }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>Needs your attention</h2>
        <Link to="/review" className="small">Review queue</Link>
      </div>
      {items.length === 0 ? (
        <div className="all-clear">
          <span className="activity-icon ok"><Icon name="checkCircle" size={16} /></span>
          <div>
            <div className="activity-title">Nothing needs a closer look</div>
            <div className="muted small">Drafts waiting for review passed every accuracy check and score 70+ for SEO.</div>
          </div>
        </div>
      ) : (
        <ul className="attention">
          {items.map((item) => (
            <li key={`${item.product_id}-${item.language}`}>
              <Link to={`/catalogs/${item.catalog_id}/products/${item.product_id}?lang=${item.language}`}>
                <div className="attention-main">
                  <span className="activity-title">{item.name}</span>
                  <span className="muted small"> · {item.sku} · {item.language.toUpperCase()}</span>
                  <div className="muted small attention-msg">{item.message ?? `SEO score ${item.seo_score}: below 70`}</div>
                </div>
                <div className="attention-tags">
                  {item.flags.slice(0, 2).map((f) => (
                    <span key={f} className={`badge ${f === "unverified_facts" || f === "wrong_language" ? "rejected" : "warn"}`}>
                      {FLAG_LABELS[f] ?? f}
                    </span>
                  ))}
                  <span className={`score ${item.seo_score >= 80 ? "good" : item.seo_score >= 60 ? "ok" : "bad"}`}>{item.seo_score}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function QualityCard({ quality, trend }: { quality: Dashboard["quality"]; trend: Dashboard["trend"] }) {
  const { low, medium, high } = quality.seo_buckets;
  const total = quality.total || 1;
  const flags = Object.entries(quality.flags);
  return (
    <div className="card">
      <div className="card-head">
        <h2>Content quality</h2>
        <Link to="/reports" className="small">Insights</Link>
      </div>
      {quality.total === 0 ? (
        <p className="muted">Quality numbers appear after your first descriptions are generated.</p>
      ) : (
        <>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="stat-label">SEO scores</span>
            <span className="small muted">{quality.clean_share}% passed every check</span>
          </div>
          <div className="seo-split" role="img" aria-label={`${high} strong, ${medium} fair, ${low} weak`}>
            {high > 0 && <div className="good" style={{ width: `${(high / total) * 100}%` }} />}
            {medium > 0 && <div className="ok" style={{ width: `${(medium / total) * 100}%` }} />}
            {low > 0 && <div className="bad" style={{ width: `${(low / total) * 100}%` }} />}
          </div>
          <div className="row small" style={{ gap: 14, marginBottom: 14 }}>
            <span><span className="score good">{high}</span> strong (80+)</span>
            <span><span className="score ok">{medium}</span> fair (60-79)</span>
            <span><span className="score bad">{low}</span> weak</span>
          </div>
          {flags.length > 0 && (
            <div className="flag-list">
              {flags.map(([kind, count]) => (
                <div key={kind} className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
                  <span className="small">{FLAG_LABELS[kind] ?? kind}</span>
                  <b className="small">{count}</b>
                </div>
              ))}
            </div>
          )}
        </>
      )}
      <div className="stat-label" style={{ margin: "16px 0 6px" }}>Last 14 days</div>
      <TrendBars trend={trend} />
    </div>
  );
}

function TrendBars({ trend }: { trend: Dashboard["trend"] }) {
  const W = 420, H = 90, max = Math.max(1, ...trend.flatMap((d) => [d.generated, d.approved]));
  const slot = W / trend.length, bar = Math.max(3, slot / 2 - 3);
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H + 16}`} width="100%" role="img" aria-label="Descriptions generated and approved per day">
        <line x1={0} x2={W} y1={H} y2={H} stroke="var(--grid)" />
        {trend.map((d, i) => {
          const x = i * slot + slot / 2;
          const gh = (d.generated / max) * (H - 6), ah = (d.approved / max) * (H - 6);
          const label = new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
          return (
            <g key={d.date}>
              <title>{`${label}: ${d.generated} generated, ${d.approved} approved`}</title>
              <rect x={x - bar - 1} y={H - gh} width={bar} height={gh} rx={2} fill="var(--series-1)" />
              <rect x={x + 1} y={H - ah} width={bar} height={ah} rx={2} fill="var(--series-3)" />
              {(i === 0 || i === trend.length - 1) && (
                <text x={x} y={H + 13} textAnchor="middle" fontSize="10" fill="var(--muted)">{label}</text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="row small" style={{ gap: 14 }}>
        <span className="row" style={{ gap: 6 }}><span className="legend-dot" style={{ background: "var(--series-1)" }} />Generated</span>
        <span className="row" style={{ gap: 6 }}><span className="legend-dot" style={{ background: "var(--series-3)" }} />Approved</span>
      </div>
    </>
  );
}

function CatalogProgress({ catalogs }: { catalogs: CatalogCard[] }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>Catalog progress</h2>
        <Link to="/catalogs" className="small">All catalogs</Link>
      </div>
      {catalogs.length === 0 ? <p className="muted">No catalogs yet.</p> : (
        <div className="stack" style={{ gap: 14 }}>
          {catalogs.slice(0, 5).map((c) => {
            const written = Math.min(100, c.product_count ? Math.round((c.generated / c.product_count) * 100) : 0);
            const approved = Math.min(100, c.product_count ? Math.round((c.approved / c.product_count) * 100) : 0);
            return (
              <Link key={c.id} to={`/catalogs/${c.id}`} style={{ color: "inherit" }}>
                <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
                  <span className="activity-title" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                  <span className="small muted">{c.approved}/{c.product_count} approved</span>
                </div>
                <div className="dual-progress" title={`${written}% written, ${approved}% approved`}>
                  <div className="written" style={{ width: `${written}%` }} />
                  <div className="approved" style={{ width: `${approved}%` }} />
                </div>
              </Link>
            );
          })}
          <div className="row small muted" style={{ gap: 14 }}>
            <span className="row" style={{ gap: 6 }}><span className="legend-dot" style={{ background: "#c7d2fe" }} />Written</span>
            <span className="row" style={{ gap: 6 }}><span className="legend-dot" style={{ background: "var(--success-500)" }} />Approved</span>
          </div>
        </div>
      )}
    </div>
  );
}
