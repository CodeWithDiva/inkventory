import { ReactNode, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ProductRow } from "../api";
import { can, Empty, ErrorAlert, Loading, ProductThumb, SeoScore, StatusBadge, useMe, useOptions } from "../components";
import { Icon } from "../icons";

const PAGE_SIZE = 25;

function Filters({ search, setSearch, catalogId, setCatalogId, language, setLanguage, children }: {
  search: string; setSearch: (v: string) => void; catalogId: string; setCatalogId: (v: string) => void;
  language: string; setLanguage: (v: string) => void; children?: ReactNode;
}) {
  const { data: catalogs } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs });
  const { data: options } = useOptions();
  return (
    <div className="toolbar">
      <div className="top-search">
        <Icon name="search" size={16} />
        <input placeholder="Search by name or SKU…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search products" />
      </div>
      <div className="spacer" />
      {children}
      <select value={catalogId} onChange={(e) => setCatalogId(e.target.value)} aria-label="Catalog">
        <option value="">All catalogs</option>
        {catalogs?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Language">
        {Object.entries(options?.languages ?? { en: "English" }).map(([c, n]) => <option key={c} value={c}>{n}</option>)}
      </select>
    </div>
  );
}

function useDebounced(value: string, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Pager({ page, total, setPage }: { page: number; total: number; setPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <div className="row" style={{ justifyContent: "space-between", marginTop: 14 }}>
      <span className="muted small">{total.toLocaleString()} product{total === 1 ? "" : "s"}</span>
      {pages > 1 && (
        <div className="row">
          <button className="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="small muted">Page {page} of {pages}</span>
          <button className="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </div>
  );
}

function ProductCell({ p }: { p: ProductRow }) {
  return (
    <div className="product-cell">
      <ProductThumb productId={p.id} src={p.images[0]} size={40} />
      <div style={{ minWidth: 0 }}>
        <div className="name">{p.name}</div>
        {p.catalog_name && <div className="muted small">{p.catalog_name}</div>}
      </div>
    </div>
  );
}

const productLink = (p: ProductRow, language: string) => `/catalogs/${p.catalog_id}/products/${p.id}?lang=${language}`;

/** Every product in the workspace, across catalogs. */
export function ProductsPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [catalogId, setCatalogId] = useState("");
  const [language, setLanguage] = useState("en");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setSearch(params.get("search") ?? ""), [params]);
  useEffect(() => setPage(1), [q, catalogId, language, status]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["all-products", q, catalogId, language, status, page],
    queryFn: () => api.allProducts({ search: q, catalog_id: catalogId, language, status, page, page_size: PAGE_SIZE }),
    placeholderData: (prev) => prev,
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Products</h1>
          <p className="muted">Every product across your catalogs, with the status of its description.</p>
        </div>
        <Link to="/catalogs/upload" className="btn primary"><Icon name="plus" size={16} />Add products</Link>
      </div>
      <Filters {...{ search, setSearch, catalogId, setCatalogId, language, setLanguage }}>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          <option value="missing">No description</option>
          <option value="pending">Waiting for review</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="warnings">Flagged by checks</option>
        </select>
      </Filters>
      <ErrorAlert error={error} />
      <div className="card" style={{ padding: 0 }}>
        {isLoading ? <Loading /> : !data?.items.length ? (
          <Empty icon="box" title="No products found">Try another search or filter, or upload a catalog.</Empty>
        ) : (
          <div className="table-wrap" style={{ border: "none" }}>
            <table>
              <thead>
                <tr><th>Product</th><th>SKU</th><th>Category</th><th>Description</th><th>SEO</th><th /></tr>
              </thead>
              <tbody>
                {data.items.map((p) => {
                  const v = p.current[language];
                  return (
                    <tr key={p.id} className="clickable" onClick={() => navigate(productLink(p, language))}>
                      <td><ProductCell p={p} /></td>
                      <td className="small muted">{p.sku}</td>
                      <td className="small">{p.category ?? "—"}</td>
                      <td>{v ? <StatusBadge status={v.status} /> : <span className="badge">Not written</span>}</td>
                      <td>{v ? <SeoScore score={v.seo_score} /> : <span className="muted">—</span>}</td>
                      <td style={{ textAlign: "right" }}><Icon name="chevronRight" size={16} className="muted" /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {data && <Pager page={page} total={data.total} setPage={setPage} />}
    </>
  );
}

const REVIEW_TABS = [
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
] as const;

/** One queue for reviewers: AI drafts from every catalog, with bulk approve / reject. */
export function ReviewPage() {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<(typeof REVIEW_TABS)[number]["id"]>("pending");
  const [search, setSearch] = useState("");
  const [catalogId, setCatalogId] = useState("");
  const [language, setLanguage] = useState("en");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<number[]>([]);
  const q = useDebounced(search);
  useEffect(() => { setPage(1); setSelected([]); }, [q, catalogId, language, tab]);

  const base = { search: q, catalog_id: catalogId, language };
  const { data, isLoading, error } = useQuery({
    queryKey: ["review", tab, q, catalogId, language, page],
    queryFn: () => api.allProducts({ ...base, status: tab, page, page_size: PAGE_SIZE }),
    placeholderData: (prev) => prev,
  });
  const counts = useQuery({
    queryKey: ["review-counts", q, catalogId, language],
    queryFn: async () => {
      const totals = await Promise.all(REVIEW_TABS.map((t) => api.allProducts({ ...base, status: t.id, page_size: 1 })));
      return Object.fromEntries(REVIEW_TABS.map((t, i) => [t.id, totals[i].total])) as Record<string, number>;
    },
  });

  const review = useMutation({
    mutationFn: (action: "approve" | "reject") => {
      const ids = (data?.items ?? []).filter((p) => selected.includes(p.id)).map((p) => p.current[language]?.id).filter(Boolean) as number[];
      return api.bulkReview(ids, action);
    },
    onSuccess: () => {
      setSelected([]);
      queryClient.invalidateQueries({ queryKey: ["review"] });
      queryClient.invalidateQueries({ queryKey: ["review-counts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["catalogs"] });
    },
  });

  const items = data?.items ?? [];
  const allOnPage = items.length > 0 && items.every((p) => selected.includes(p.id));
  const toggle = (id: number) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const canReview = can(me, "review");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Review & approve</h1>
          <p className="muted">Check AI-generated descriptions, edit if needed, and approve them for export.</p>
        </div>
      </div>

      <div className="tabs" role="tablist">
        {REVIEW_TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}<span className="count">({(counts.data?.[t.id] ?? 0).toLocaleString()})</span>
          </button>
        ))}
      </div>

      <Filters {...{ search, setSearch, catalogId, setCatalogId, language, setLanguage }}>
        {canReview && tab !== "approved" && selected.length > 0 && (
          <>
            <span className="small muted">{selected.length} selected</span>
            <button className="success small" disabled={review.isPending} onClick={() => review.mutate("approve")}>
              <Icon name="check" size={14} />Approve
            </button>
            {tab === "pending" && (
              <button className="danger small" disabled={review.isPending} onClick={() => review.mutate("reject")}>
                <Icon name="x" size={14} />Reject
              </button>
            )}
          </>
        )}
      </Filters>
      <ErrorAlert error={error || review.error} />
      {review.data && (
        <div className="alert success">
          <Icon name="checkCircle" size={16} />
          <span>
            {review.data.updated} updated
            {review.data.awaiting_final_approval ? ` · ${review.data.awaiting_final_approval} now wait for final approval by an admin` : ""}
            {review.data.skipped ? ` · ${review.data.skipped} skipped` : ""}
          </span>
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        {isLoading ? <Loading /> : !items.length ? (
          <Empty icon="checkCircle" title={tab === "pending" ? "All caught up" : `No ${tab} descriptions`}>
            {tab === "pending" ? "There is nothing waiting for review in this language." : "Try another catalog or language."}
          </Empty>
        ) : (
          <div className="table-wrap" style={{ border: "none" }}>
            <table>
              <thead>
                <tr>
                  {canReview && tab !== "approved" && (
                    <th style={{ width: 36 }}>
                      <input type="checkbox" aria-label="Select all on this page" checked={allOnPage}
                        onChange={() => setSelected(allOnPage ? [] : items.map((p) => p.id))} />
                    </th>
                  )}
                  <th>Product</th><th>SKU</th><th>Status</th><th>SEO</th><th>Checks</th><th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => {
                  const v = p.current[language];
                  return (
                    <tr key={p.id}>
                      {canReview && tab !== "approved" && (
                        <td><input type="checkbox" aria-label={`Select ${p.name}`} checked={selected.includes(p.id)} onChange={() => toggle(p.id)} /></td>
                      )}
                      <td><ProductCell p={p} /></td>
                      <td className="small muted">{p.sku}</td>
                      <td>{v && <StatusBadge status={v.status} />}</td>
                      <td>{v && <SeoScore score={v.seo_score} />}</td>
                      <td className="small">
                        {v?.warnings.length
                          ? <span className="badge warn" title={v.warnings.map((w) => w.message).join("\n")}>{v.warnings.length} flag{v.warnings.length > 1 ? "s" : ""}</span>
                          : <span className="muted">Passed</span>}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <button className="small" onClick={() => navigate(productLink(p, language))}>
                          {tab === "pending" ? "Review" : "View"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {data && <Pager page={page} total={data.total} setPage={setPage} />}
    </>
  );
}
