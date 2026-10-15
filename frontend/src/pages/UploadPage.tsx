import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorAlert } from "../components";
import { Icon } from "../icons";

const FIELDS: { key: string; label: string; required?: boolean; hint: string }[] = [
  { key: "sku", label: "SKU", required: true, hint: "Unique product code" },
  { key: "name", label: "Product name", required: true, hint: "Product title" },
  { key: "category", label: "Category", hint: "Helps the AI understand the product" },
  { key: "brand", label: "Brand", hint: "" },
  { key: "price", label: "Price", hint: "Stored only; never written into copy" },
  { key: "keywords", label: "SEO keywords", hint: "Comma separated; first is primary" },
  { key: "description", label: "Existing description", hint: "Used as extra context" },
  { key: "images", label: "Image URLs", hint: "Photo links; AI that can see images uses them" },
];

type Preview = Awaited<ReturnType<typeof api.previewCatalog>>;
type Result = Awaited<ReturnType<typeof api.uploadCatalog>>;

export default function UploadPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const [source, setSource] = useState<"file" | "url">(params.get("tab") === "sheet" ? "url" : "file");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const choose = async (f: File | null) => {
    setFile(f);
    setPreview(null);
    setResult(null);
    setError(null);
    if (!f) return;
    setName((n) => n || f.name.replace(/\.[^.]+$/, ""));
    setBusy(true);
    try {
      const p = await api.previewCatalog(f);
      setPreview(p);
      setMapping(p.suggested_mapping);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const previewFromUrl = async () => {
    setPreview(null);
    setResult(null);
    setError(null);
    setBusy(true);
    try {
      const p = await api.previewUrl(url);
      setPreview(p);
      setMapping(p.suggested_mapping);
      setName((n) => n || "Imported from Google Sheets");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const upload = async () => {
    if (source === "url") {
      setBusy(true);
      setError(null);
      try {
        const clean = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v));
        setResult(await api.importUrl(url, name, clean));
        queryClient.invalidateQueries({ queryKey: ["catalogs"] });
      } catch (err) {
        setError(err);
      } finally {
        setBusy(false);
      }
      return;
    }
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const clean = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v));
      const r = await api.uploadCatalog(file, name, clean);
      setResult(r);
      queryClient.invalidateQueries({ queryKey: ["catalogs"] });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const mappedColumns = new Set(Object.values(mapping).filter(Boolean));
  const attributeColumns = preview?.headers.filter((h) => !mappedColumns.has(h)) ?? [];
  const missingRequired = FIELDS.filter((f) => f.required && !mapping[f.key]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Upload catalog</h1>
          <p className="muted">CSV with one product per row. Excel: File → Save As → CSV UTF-8.</p>
        </div>
        <Link to="/catalogs"><button>Back</button></Link>
      </div>
      <ErrorAlert error={error} />

      {result ? (
        <div className="card">
          <div className="alert success">
            Imported {result.imported} new products{result.updated ? `, updated ${result.updated}` : ""}.
          </div>
          {result.errors.length > 0 && (
            <>
              <div className="alert warning">{result.errors.length} rows were skipped:</div>
              <div className="table-wrap" style={{ maxHeight: 240, overflow: "auto" }}>
                <table>
                  <thead><tr><th>Row</th><th>SKU</th><th>Problem</th></tr></thead>
                  <tbody>
                    {result.errors.map((e, i) => (
                      <tr key={i}><td>{e.row}</td><td>{e.sku ?? "—"}</td><td>{e.error}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={() => navigate(`/catalogs/${result.catalog.id}`)}>
              Open catalog &amp; generate descriptions
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="card">
            <h2>1. Choose your product list</h2>
            <div className="tabs">
              <button className={`tab ${source === "file" ? "active" : ""}`} onClick={() => { setSource("file"); setPreview(null); }}>CSV file</button>
              <button className={`tab ${source === "url" ? "active" : ""}`} onClick={() => { setSource("url"); setPreview(null); }}>Google Sheets / URL</button>
            </div>
            <div className="grid grid-2">
              {source === "file" ? (
                <div className="field">
                  <label>CSV file</label>
                  <input type="file" accept=".csv,.tsv,.txt" onChange={(e) => choose(e.target.files?.[0] ?? null)} />
                </div>
              ) : (
                <div className="field">
                  <label>Google Sheets link or CSV URL</label>
                  <div className="row" style={{ flexWrap: "nowrap" }}>
                    <input placeholder="https://docs.google.com/spreadsheets/d/…" value={url} onChange={(e) => setUrl(e.target.value)} />
                    <button disabled={!url.startsWith("http") || busy} onClick={previewFromUrl}>Load</button>
                  </div>
                  <p className="muted small" style={{ marginTop: 4 }}>In Google Sheets: Share → General access → "Anyone with the link".</p>
                </div>
              )}
              <div className="field">
                <label>Catalog name</label>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Summer collection 2026" />
              </div>
            </div>
            {busy && !preview && <p className="muted">Reading file…</p>}
          </div>

          {preview && (
            <div className="card">
              <h2>2. Match your columns</h2>
              {preview.detected_format === "shopify" && (
                <div className="alert info">
                  <Icon name="info" size={16} />
                  <span>
                    Shopify product export detected: {preview.row_count} rows describe <b>{preview.product_count}</b> products.
                    Variant and image rows are merged into their product, photos and option values (e.g. colors) are
                    kept, and products without a SKU use their Shopify handle.
                  </span>
                </div>
              )}
              <p className="muted">
                Found {preview.row_count} rows. We guessed the columns below — adjust if needed. Unmapped columns
                ({attributeColumns.length}) are kept as product attributes for the AI: {" "}
                {attributeColumns.map((c) => <span className="chip" key={c}>{c}</span>)}
              </p>
              <table className="mapping-table">
                <thead><tr><th>Field</th><th>Column in your file</th><th>Example</th></tr></thead>
                <tbody>
                  {FIELDS.map((f) => (
                    <tr key={f.key}>
                      <td>
                        <b>{f.label}</b>{f.required && <span style={{ color: "var(--danger)" }}> *</span>}
                        <div className="muted small">{f.hint}</div>
                      </td>
                      <td style={{ width: 260 }}>
                        <select value={mapping[f.key] ?? ""} onChange={(e) => setMapping({ ...mapping, [f.key]: e.target.value })}>
                          <option value="">— not in file —</option>
                          {preview.headers.map((h) => <option key={h} value={h}>{h}</option>)}
                        </select>
                      </td>
                      <td className="muted">{mapping[f.key] ? preview.sample_rows[0]?.[mapping[f.key]] : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {missingRequired.length > 0 && (
                <div className="alert warning" style={{ marginTop: 12 }}>
                  Please choose a column for: {missingRequired.map((f) => f.label).join(", ")}
                </div>
              )}
              <div className="row" style={{ marginTop: 12 }}>
                <button className="primary" disabled={busy || missingRequired.length > 0 || !name.trim()} onClick={upload}>
                  {busy ? "Importing…" : preview.product_count != null && preview.product_count !== preview.row_count
                    ? `Import ${preview.product_count} products` : `Import ${preview.row_count} rows`}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}
