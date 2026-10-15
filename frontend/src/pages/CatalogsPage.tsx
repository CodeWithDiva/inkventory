import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, CatalogCard } from "../api";
import { can, ErrorAlert, Loading, ProductThumb, useMe } from "../components";
import { Icon } from "../icons";

const SOURCES: Record<string, string> = { csv: "CSV", url: "Google Sheets", api: "API" };

export function sourceLabel(c: { source: string; source_filename: string | null }) {
  if (c.source_filename?.startsWith("shopify")) return "Shopify";
  if (c.source_filename?.startsWith("woocommerce")) return "WooCommerce";
  return SOURCES[c.source] ?? c.source;
}

/** One status per catalog, in the words a catalog manager uses. */
export function catalogStatus(c: CatalogCard): string {
  if (c.last_job_status === "running" || c.last_job_status === "pending") return "running";
  if (c.product_count > 0 && c.approved >= c.product_count) return "approved";
  if (c.pending_review > 0) return "in_review";
  if (c.generated === 0) return "pending";
  return "completed";
}

const STATUS_TEXT: Record<string, string> = {
  running: "Generating", approved: "Approved", in_review: "Needs review", pending: "Not started", completed: "Generated",
};

export function CatalogCover({ catalog }: { catalog: CatalogCard }) {
  const thumbs = catalog.thumbnails;
  if (!thumbs.length) {
    return <div className="catalog-cover empty"><Icon name="box" size={30} /></div>;
  }
  return (
    <div className={`catalog-cover ${thumbs.length === 1 ? "single" : ""}`}>
      {thumbs.slice(0, 2).map((t) => (
        <ProductThumb key={t.product_id} productId={t.product_id} index={t.index} src={t.url ?? ""} size="fill" />
      ))}
    </div>
  );
}

export default function CatalogsPage() {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [sort, setSort] = useState("newest");
  const { data, isLoading, error } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs, refetchInterval: 15000 });
  const remove = useMutation({
    mutationFn: api.deleteCatalog,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["catalogs"] }),
  });

  const list = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = (data ?? []).filter((c) => (!q || c.name.toLowerCase().includes(q)) && (!source || sourceLabel(c) === source));
    return [...filtered].sort((a, b) =>
      sort === "name" ? a.name.localeCompare(b.name)
        : sort === "oldest" ? a.created_at.localeCompare(b.created_at)
          : sort === "size" ? b.product_count - a.product_count
            : b.created_at.localeCompare(a.created_at));
  }, [data, search, source, sort]);
  const sources = [...new Set((data ?? []).map(sourceLabel))];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Catalogs</h1>
          <p className="muted">Manage your product catalogs and track their progress.</p>
        </div>
        {can(me, "edit") && <Link to="/catalogs/upload" className="btn primary"><Icon name="plus" size={16} />New catalog</Link>}
      </div>
      <ErrorAlert error={error || remove.error} />

      <div className="toolbar">
        <div className="top-search">
          <Icon name="search" size={16} />
          <input placeholder="Search catalogs…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search catalogs" />
        </div>
        <div className="spacer" />
        <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
          <option value="">All sources</option>
          {sources.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="name">Name A–Z</option>
          <option value="size">Most products</option>
        </select>
      </div>

      {isLoading ? <Loading /> : (
        <div className="catalog-grid">
          {can(me, "edit") && !search && !source && (
            <Link to="/catalogs/upload" className="catalog-card create">
              <span className="create-icon"><Icon name="plus" size={20} /></span>
              <strong>Create new catalog</strong>
              <span className="small">Upload a file, connect a store or add via URL</span>
            </Link>
          )}
          {list.map((c) => {
            const status = catalogStatus(c);
            const pct = c.product_count ? Math.round((c.approved / c.product_count) * 100) : 0;
            return (
              <div key={c.id} className="catalog-card" role="link" tabIndex={0}
                onClick={() => navigate(`/catalogs/${c.id}`)}
                onKeyDown={(e) => e.key === "Enter" && navigate(`/catalogs/${c.id}`)}>
                <CatalogCover catalog={c} />
                {can(me, "edit") && (
                  <button className="card-menu danger" title="Delete catalog" aria-label={`Delete ${c.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Delete "${c.name}" and all its descriptions? This cannot be undone.`)) remove.mutate(c.id);
                    }}>
                    <Icon name="x" size={14} />
                  </button>
                )}
                <h3>{c.name}</h3>
                <div className="meta">{sourceLabel(c)} · {c.product_count.toLocaleString()} products</div>
                <div className="mini-progress" title={`${pct}% approved`}><div style={{ width: `${pct}%` }} /></div>
                <div className="foot">
                  <span className={`badge dot ${status === "pending" ? "cancelled" : status}`}>{STATUS_TEXT[status]}</span>
                  <span>{new Date(c.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</span>
                </div>
              </div>
            );
          })}
          {!list.length && (search || source) && <p className="muted">No catalogs match your filters.</p>}
          {data && data.length > 0 && (
            <div className="catalog-card create" style={{ cursor: "default" }}>
              <span className="create-icon" style={{ borderColor: "var(--border-strong)", color: "var(--muted)" }}><Icon name="folder" size={18} /></span>
              <strong>{data.length} catalog{data.length === 1 ? "" : "s"}</strong>
              <span className="small">{data.reduce((n, c) => n + c.product_count, 0).toLocaleString()} products in total</span>
            </div>
          )}
        </div>
      )}
    </>
  );
}
