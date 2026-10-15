import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Empty, ErrorAlert, Loading } from "../components";
import { Icon } from "../icons";
import { ExportTab } from "./CatalogDetailPage";

export default function ExportPage() {
  const { data: catalogs, isLoading, error } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs });
  const [catalogId, setCatalogId] = useState<number | null>(null);
  useEffect(() => {
    if (catalogId === null && catalogs?.length) {
      setCatalogId((catalogs.find((c) => c.approved > 0) ?? catalogs[0]).id);
    }
  }, [catalogs, catalogId]);
  const selected = catalogs?.find((c) => c.id === catalogId);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Export & publish</h1>
          <p className="muted">Download marketplace-ready files or push approved descriptions straight to your store.</p>
        </div>
        <Link to="/integrations" className="btn"><Icon name="link" size={16} />Store connections</Link>
      </div>
      <ErrorAlert error={error} />
      {isLoading ? <Loading /> : !catalogs?.length ? (
        <div className="card"><Empty icon="send" title="Nothing to export yet">Upload a catalog, generate and approve descriptions first.</Empty></div>
      ) : (
        <>
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="row" style={{ gap: 16 }}>
              <div style={{ minWidth: 260 }}>
                <label htmlFor="export-catalog">Catalog</label>
                <select id="export-catalog" value={catalogId ?? ""} onChange={(e) => setCatalogId(Number(e.target.value))}>
                  {catalogs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              {selected && (
                <div className="row" style={{ gap: 24, marginTop: 20 }}>
                  <span><b>{selected.approved.toLocaleString()}</b> <span className="muted">approved</span></span>
                  <span><b>{selected.pending_review.toLocaleString()}</b> <span className="muted">waiting for review</span></span>
                  <span><b>{selected.product_count.toLocaleString()}</b> <span className="muted">products</span></span>
                </div>
              )}
            </div>
            {selected && selected.approved === 0 && (
              <div className="alert info" style={{ margin: "14px 0 0" }}>
                <Icon name="info" size={16} />
                <span>This catalog has no approved descriptions yet. <Link to="/review">Review & approve</Link> them first.</span>
              </div>
            )}
          </div>
          {catalogId !== null && <ExportTab key={catalogId} catalogId={catalogId} />}
        </>
      )}
    </>
  );
}
