import { Fragment } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorAlert, Loading, ProductThumb } from "../components";
import { Icon, IconName } from "../icons";
import { catalogStatus, sourceLabel } from "./CatalogsPage";

export function Stepper({ steps, active }: { steps: string[]; active: number }) {
  return (
    <div className="stepper">
      {steps.map((s, i) => (
        <Fragment key={s}>
          {i > 0 && <span className="step-line" />}
          <span className={`step ${i === active ? "active" : ""}`}>
            <span className="step-num">{i + 1}</span>{s}
          </span>
        </Fragment>
      ))}
    </div>
  );
}

export const SOURCES: { to: string; icon: IconName; cls: string; title: string; text: string }[] = [
  { to: "/integrations", icon: "store", cls: "shopify", title: "Shopify", text: "Connect your store and sync products" },
  { to: "/integrations", icon: "store", cls: "woo", title: "WooCommerce", text: "Connect your store and sync products" },
  { to: "/catalogs/upload?tab=sheet", icon: "sheet", cls: "sheets", title: "Google Sheets", text: "Import from a shared sheet link" },
  { to: "/developer", icon: "code", cls: "api", title: "API", text: "Send products from your own system" },
];

/** Step 1 of generation: pick the catalog; configuration happens on the catalog's Generate tab. */
export default function GeneratePage() {
  const navigate = useNavigate();
  const { data: catalogs, isLoading, error } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Generate descriptions</h1>
          <p className="muted">Choose your catalog, style and languages, and let AI write unique, SEO-friendly descriptions.</p>
        </div>
      </div>
      <Stepper steps={["Select catalog", "Configure", "Generate", "Review"]} active={0} />
      <ErrorAlert error={error} />

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.5fr) minmax(280px, 1fr)", alignItems: "stretch" }}>
        <div className="card">
          <div className="card-head"><h2>Select a catalog</h2></div>
          {isLoading ? <Loading /> : catalogs && catalogs.length > 0 ? (
            <div className="pick-list">
              {catalogs.map((c) => {
                const missing = Math.max(0, c.product_count - c.generated);
                return (
                  <button key={c.id} className="pick" onClick={() => navigate(`/catalogs/${c.id}?tab=generate`)}>
                    {c.thumbnails[0]
                      ? <ProductThumb productId={c.thumbnails[0].product_id} src={c.thumbnails[0].url ?? ""} size={44} />
                      : <span className="thumb" style={{ width: 44, height: 44 }}><Icon name="box" size={18} /></span>}
                    <span>
                      <strong>{c.name}</strong>
                      <span className="muted small">
                        {sourceLabel(c)} · {c.product_count.toLocaleString()} products ·{" "}
                        {catalogStatus(c) === "running" ? "generating now"
                          : missing ? `${missing.toLocaleString()} without a description` : "all products have descriptions"}
                      </span>
                    </span>
                    <Icon name="chevronRight" size={18} />
                  </button>
                );
              })}
              <Link to="/catalogs/upload" className="pick">
                <span className="thumb" style={{ width: 44, height: 44, background: "var(--primary-soft)", color: "var(--primary)" }}>
                  <Icon name="plus" size={18} />
                </span>
                <span><strong>Upload a new catalog</strong><span className="muted small">CSV or Excel export, up to 50,000 rows</span></span>
                <Icon name="chevronRight" size={18} />
              </Link>
            </div>
          ) : (
            <div className="dropzone">
              <span className="drop-icon"><Icon name="upload" size={24} /></span>
              <strong>Upload your first catalog</strong>
              <span className="small">CSV file, Google Sheets link or a connected store</span>
              <Link to="/catalogs/upload" className="btn primary" style={{ marginTop: 8 }}>Upload file</Link>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head"><h2>Other sources</h2></div>
          <div className="pick-list">
            {SOURCES.map((s) => (
              <Link key={s.title} to={s.to} className="pick">
                <span className={`source-icon ${s.cls}`}><Icon name={s.icon} size={18} /></span>
                <span><strong>{s.title}</strong><span className="muted small">{s.text}</span></span>
                <Icon name="chevronRight" size={18} />
              </Link>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
