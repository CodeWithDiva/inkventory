import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Product, Version } from "../api";
import { AssigneeSelect, Comments } from "../Collaboration";
import { Icon } from "../icons";
import {
  can, EngineSelect, Empty, ErrorAlert, formatDate, Loading, pickDefaultEngine, ProductThumb, SeoRing, SeoScore, StatusBadge,
  useMe, useOptions,
} from "../components";

type Draft = Pick<Version, "title" | "short_description" | "long_description" | "bullet_points" | "meta_title" | "meta_description">;

const toDraft = (v: Version): Draft => ({
  title: v.title,
  short_description: v.short_description,
  long_description: v.long_description,
  bullet_points: v.bullet_points,
  meta_title: v.meta_title,
  meta_description: v.meta_description,
});

export default function ProductReviewPage() {
  const { catalogId, productId } = useParams();
  const pid = Number(productId);
  const [params, setParams] = useSearchParams();
  const language = params.get("lang") ?? "en";
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: options } = useOptions();

  const productQuery = useQuery({ queryKey: ["product", pid], queryFn: () => api.product(pid) });
  const orgSettings = useQuery({ queryKey: ["org-settings"], queryFn: api.orgSettings });
  const versionsQuery = useQuery({ queryKey: ["versions", pid, language], queryFn: () => api.versions(pid, language) });
  const versions = versionsQuery.data ?? [];
  const current = versions.find((v) => v.is_current);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [note, setNote] = useState("");
  const [extra, setExtra] = useState("");
  const [engine, setEngine] = useState<string | undefined>();
  const [useImages, setUseImages] = useState(true);
  const [view, setView] = useState<"generated" | "original" | "compare">("generated");
  useEffect(() => {
    if (engine === undefined) setEngine(pickDefaultEngine(options));
  }, [options, engine]);
  useEffect(() => setDraft(current ? toDraft(current) : null), [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const afterChange = () => {
    queryClient.invalidateQueries({ queryKey: ["versions", pid] });
    queryClient.invalidateQueries({ queryKey: ["products", Number(catalogId)] });
    queryClient.invalidateQueries({ queryKey: ["catalog", Number(catalogId)] });
  };
  const save = useMutation({ mutationFn: () => api.editVersion(current!.id, draft!), onSuccess: afterChange });
  const approve = useMutation({ mutationFn: () => api.approve(current!.id, note || undefined), onSuccess: afterChange });
  const reject = useMutation({ mutationFn: () => api.reject(current!.id, note || undefined), onSuccess: afterChange });
  const regenerate = useMutation({
    // The server reuses the preset of the current version when none is given.
    mutationFn: () => api.regenerate(pid, { language, engine: engine ?? null, extra_instructions: extra, use_images: useImages }),
    onSuccess: afterChange,
  });
  const restore = useMutation({ mutationFn: (id: number) => api.restoreVersion(id), onSuccess: afterChange });
  const pick = useMutation({ mutationFn: (id: number) => api.makeCurrent(id), onSuccess: afterChange });

  if (productQuery.isLoading) return <Loading />;
  if (!productQuery.data) return <ErrorAlert error={productQuery.error} />;
  const product = productQuery.data;
  const dirty = !!current && !!draft && JSON.stringify(draft) !== JSON.stringify(toDraft(current));
  const anyError = save.error || approve.error || reject.error || regenerate.error || restore.error || pick.error;
  const twoStep = (orgSettings.data?.approval_steps ?? 1) >= 2;
  const isAdmin = can(me, "admin");
  const approveLabel = !current || !twoStep
    ? { text: "Approve", blocked: false, hint: "" }
    : current.status === "in_review"
      ? isAdmin
        ? { text: "Final approve", blocked: false, hint: "Second and final approval" }
        : { text: "Waiting for admin", blocked: true, hint: "A reviewer already approved this; an admin gives final approval" }
      : isAdmin
        ? { text: "Approve", blocked: false, hint: "Admins can approve directly" }
        : { text: "Approve (step 1 of 2)", blocked: false, hint: "An admin gives the final approval" };
  // Variants: other versions written by the same job as the current one.
  const variants = current?.job_id ? versions.filter((v) => v.job_id === current.job_id) : [];
  const set = (key: keyof Draft) => (e: { target: { value: string } }) =>
    setDraft({ ...draft!, [key]: key === "bullet_points" ? e.target.value.split("\n") : e.target.value });

  return (
    <>
      <div className="page-head">
        <div className="row" style={{ gap: 14, flexWrap: "nowrap" }}>
          <ProductThumb key={product.images[0] ?? "none"} productId={product.id} src={product.images[0]} size={56} />
          <div>
            <h1>{product.name}</h1>
            <p className="muted" style={{ margin: 0 }}>
              SKU {product.sku}{product.category ? ` · ${product.category}` : ""}{product.brand ? ` · ${product.brand}` : ""}
            </p>
          </div>
        </div>
        <select style={{ width: 200 }} value={language} onChange={(e) => setParams({ lang: e.target.value })}>
          {Object.entries(options?.languages ?? { en: "English" }).map(([c, n]) => <option key={c} value={c}>{n}</option>)}
        </select>
      </div>
      <ErrorAlert error={anyError} />

      <div className="review-grid">
        <div className="stack">
          {!current ? (
            <div className="card">
              <Empty>
                No {options?.languages[language] ?? language} description yet.
                {can(me, "edit") && (
                  <div style={{ marginTop: 12, maxWidth: 360, marginInline: "auto", textAlign: "left" }}>
                    <EngineSelect value={engine} onChange={setEngine} />
                    <button style={{ marginTop: 8 }} className="primary" disabled={regenerate.isPending} onClick={() => regenerate.mutate()}>
                      {regenerate.isPending ? "Generating…" : "Generate now"}
                    </button>
                  </div>
                )}
              </Empty>
            </div>
          ) : (
            draft && (
              <div className="card">
                <div className="tabs" role="tablist" style={{ marginTop: -6 }}>
                  {([["generated", "Generated"], ["original", "Original"], ["compare", "Comparison"]] as const).map(([id, label]) => (
                    <button key={id} role="tab" aria-selected={view === id} className={`tab ${view === id ? "active" : ""}`}
                      onClick={() => setView(id)}>{label}</button>
                  ))}
                </div>
                {view === "original" && (
                  <div style={{ whiteSpace: "pre-line", lineHeight: 1.65 }}>
                    {product.original_description || <span className="muted">The upload had no original description for this product.</span>}
                  </div>
                )}
                {view === "compare" && (
                  <div className="grid grid-2">
                    <div>
                      <div className="stat-label" style={{ marginBottom: 8 }}>Original</div>
                      <div className="small" style={{ whiteSpace: "pre-line", lineHeight: 1.65 }}>
                        {product.original_description || <span className="muted">No original description.</span>}
                      </div>
                    </div>
                    <div>
                      <div className="stat-label" style={{ marginBottom: 8 }}>Generated · v{current.version_no}</div>
                      <div style={{ fontWeight: 600, marginBottom: 6 }}>{current.title}</div>
                      <div className="small" style={{ whiteSpace: "pre-line", lineHeight: 1.65 }}>{current.long_description}</div>
                      {current.bullet_points.length > 0 && (
                        <ul className="small" style={{ paddingLeft: 18 }}>{current.bullet_points.map((b, i) => <li key={i}>{b}</li>)}</ul>
                      )}
                    </div>
                  </div>
                )}
                <div hidden={view !== "generated"}>
                <div className="row" style={{ marginBottom: 12 }}>
                  <h2 style={{ margin: 0 }}>Version {current.version_no}</h2>
                  <StatusBadge status={current.status} />
                  <span className="badge">{current.source === "ai" ? `AI · ${current.model}` : current.source}</span>
                  <div className="spacer" />
                  <span>SEO <SeoScore score={current.seo_score} /></span>
                </div>
                {current.warnings.map((w, i) => (
                  <div key={i} className="alert warning"><Icon name="alert" size={16} /><span>{w.message}</span></div>
                ))}
                {current.review_note && <div className="alert info">Review note: {current.review_note}</div>}

                <fieldset disabled={!can(me, "edit")} style={{ border: "none", padding: 0, margin: 0 }}>
                  <div className="field">
                    <label>Title <span className="muted small">({draft.title.length}/70)</span></label>
                    <input value={draft.title} onChange={set("title")} />
                  </div>
                  <div className="field">
                    <label>Short description</label>
                    <textarea rows={2} value={draft.short_description} onChange={set("short_description")} />
                  </div>
                  <div className="field">
                    <label>Long description <span className="muted small">({draft.long_description.split(/\s+/).filter(Boolean).length} words)</span></label>
                    <textarea rows={10} value={draft.long_description} onChange={set("long_description")} />
                  </div>
                  <div className="field">
                    <label>Bullet points <span className="muted small">(one per line)</span></label>
                    <textarea rows={5} value={draft.bullet_points.join("\n")} onChange={set("bullet_points")} />
                  </div>
                  <div className="grid grid-2">
                    <div className="field">
                      <label>Meta title <span className="muted small">({draft.meta_title.length}/60)</span></label>
                      <input value={draft.meta_title} onChange={set("meta_title")} />
                    </div>
                    <div className="field">
                      <label>Meta description <span className="muted small">({draft.meta_description.length}/160)</span></label>
                      <textarea rows={3} value={draft.meta_description} onChange={set("meta_description")} />
                    </div>
                  </div>
                </fieldset>

                <div className="row">
                  {can(me, "edit") && (
                    <>
                      <button disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
                        {save.isPending ? "Saving…" : "Save as new version"}
                      </button>
                      {dirty && <button onClick={() => setDraft(toDraft(current))}>Discard changes</button>}
                    </>
                  )}
                  <div className="spacer" />
                  {can(me, "review") && (
                    <>
                      <input style={{ width: 220 }} placeholder="Review note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
                      <button className="danger" disabled={dirty || reject.isPending} onClick={() => reject.mutate()}>Reject</button>
                      <button className="success" disabled={dirty || approve.isPending || approveLabel.blocked} title={approveLabel.hint}
                        onClick={() => approve.mutate()}>{approveLabel.text}</button>
                    </>
                  )}
                </div>
                {dirty && can(me, "review") && <p className="muted small" style={{ marginTop: 6 }}>Save your edits before approving.</p>}
                </div>
              </div>
            )
          )}

          {current && can(me, "edit") && (
            <div className="card">
              <h3>Regenerate with AI</h3>
              <div className="field">
                <EngineSelect value={engine} onChange={setEngine} />
              </div>
              <div className="field">
                <textarea rows={2} value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="Optional guidance, e.g. focus more on durability, make it shorter…" />
              </div>
              {product.images.length > 0 && (
                <label className="checkbox" style={{ marginBottom: 10 }}>
                  <input type="checkbox" checked={useImages} onChange={(e) => setUseImages(e.target.checked)} />
                  Use the {product.images.length} product photo{product.images.length > 1 ? "s" : ""}
                </label>
              )}
              <button disabled={regenerate.isPending} onClick={() => regenerate.mutate()}>
                {regenerate.isPending ? "Generating… (local AI can take a few minutes)" : "Regenerate (creates a new version)"}
              </button>
            </div>
          )}

          <Comments productId={pid} language={language} versionId={current?.id} />

          {variants.length > 1 && (
            <div className="card">
              <h3>Compare variants ({variants.length})</h3>
              <p className="muted small">The AI wrote {variants.length} versions in one job. The highest-scoring one was selected automatically.</p>
              <div className="grid grid-2">
                {variants.map((v) => (
                  <div key={v.id} className="card" style={{ marginTop: 0, borderColor: v.is_current ? "var(--primary)" : undefined }}>
                    <div className="row">
                      <b>v{v.version_no}</b>
                      <span className="small">SEO <SeoScore score={v.seo_score} /></span>
                      {v.warnings.length > 0 && <span className="badge warn"><Icon name="alert" size={12} />{v.warnings.length}</span>}
                      <div className="spacer" />
                      {v.is_current ? <span className="badge approved">in use</span> : can(me, "edit") && (
                        <button className="small" disabled={pick.isPending} onClick={() => pick.mutate(v.id)}>Use this</button>
                      )}
                    </div>
                    <div style={{ fontWeight: 600, margin: "6px 0" }}>{v.title}</div>
                    <p className="small" style={{ whiteSpace: "pre-line" }}>{v.long_description.slice(0, 420)}{v.long_description.length > 420 ? "…" : ""}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="stack">
          <div className="card"><AssigneeSelect product={product} /></div>
          <Photos product={product} canEdit={can(me, "edit")} />
          {current?.seo_report.checks && (
            <div className="card">
              <h3>SEO score</h3>
              <div className="row" style={{ gap: 18, flexWrap: "nowrap", margin: "6px 0 10px" }}>
                <SeoRing score={current.seo_score} />
                <div>
                  <div style={{ fontWeight: 600 }}>
                    {current.seo_score >= 80 ? "Strong" : current.seo_score >= 60 ? "Good, can improve" : "Needs work"}
                  </div>
                  <div className="muted small">
                    {current.seo_report.checks.filter((c) => c.passed).length} of {current.seo_report.checks.length} checks passed
                  </div>
                </div>
              </div>
              <div className="stat-label" style={{ margin: "8px 0 2px" }}>SEO checks</div>
              {current.seo_report.checks.map((c) => (
                <div className="check" key={c.id}>
                  <span className={`check-icon ${c.passed ? "pass" : "fail"}`}><Icon name={c.passed ? "check" : "x"} size={12} strokeWidth={3} /></span>
                  <div>
                    <div>{c.label}</div>
                    <div className="muted small">{c.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <ProductInfo product={product} canEdit={can(me, "edit")} />
          <div className="card">
            <h3>Version history</h3>
            {!versions.length ? (
              <p className="muted">No versions yet.</p>
            ) : (
              versions.map((v) => (
                <div className="version-item" key={v.id}>
                  <div className="row">
                    <b>v{v.version_no}</b>
                    <StatusBadge status={v.status} />
                    <span className="small muted">{v.source}</span>
                    <div className="spacer" />
                    <SeoScore score={v.seo_score} />
                  </div>
                  <div className="small">{v.title}</div>
                  <div className="row small muted">
                    {formatDate(v.created_at)}
                    <div className="spacer" />
                    {v.is_current ? (
                      <span className="badge">current</span>
                    ) : (
                      can(me, "edit") && <button className="link small" onClick={() => restore.mutate(v.id)}>Restore</button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function ProductInfo({ product, canEdit }: { product: Product; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const [keywords, setKeywords] = useState(product.keywords.join(", "));
  const save = useMutation({
    mutationFn: () =>
      api.updateProduct(product.id, { keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["product", product.id] }),
  });
  const changed = keywords !== product.keywords.join(", ");

  return (
    <div className="card">
      <h3>Product data (AI input)</h3>
      <dl className="kv">
        <dt>Category</dt><dd>{product.category ?? "—"}</dd>
        <dt>Brand</dt><dd>{product.brand ?? "—"}</dd>
        <dt>Price</dt><dd>{product.price ?? "—"}</dd>
        {Object.entries(product.attributes).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v}</dd></div>
        ))}
      </dl>
      <div className="field" style={{ marginTop: 12 }}>
        <label>SEO keywords <span className="muted small">(comma separated, first = primary)</span></label>
        <input value={keywords} disabled={!canEdit} onChange={(e) => setKeywords(e.target.value)} />
      </div>
      {changed && (
        <button className="small" disabled={save.isPending} onClick={() => save.mutate()}>Save keywords</button>
      )}
      <ErrorAlert error={save.error} />
    </div>
  );
}

function Photos({ product, canEdit }: { product: Product; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [sources, setSources] = useState<Record<number, string>>({});

  // Uploaded photos need the auth header, so they are fetched as blobs; external URLs load directly.
  useEffect(() => {
    let cancelled = false;
    const created: string[] = [];
    Promise.all(
      product.images.map(async (ref, i) => {
        if (ref.startsWith("http")) return [i, ref] as const;
        const objectUrl = await api.imageObjectUrl(product.id, i).catch(() => "");
        if (objectUrl) created.push(objectUrl);
        return [i, objectUrl] as const;
      }),
    ).then((pairs) => !cancelled && setSources(Object.fromEntries(pairs)));
    return () => {
      cancelled = true;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [product.id, product.images]);

  const refresh = (p: Product) => queryClient.setQueryData(["product", product.id], p);
  const upload = useMutation({ mutationFn: (file: File) => api.uploadImage(product.id, file), onSuccess: refresh });
  const addUrl = useMutation({ mutationFn: () => api.addImageUrl(product.id, url), onSuccess: (p) => { setUrl(""); refresh(p); } });
  const remove = useMutation({ mutationFn: (i: number) => api.deleteImage(product.id, i), onSuccess: refresh });
  const analyze = useMutation({ mutationFn: () => api.analyzePhotos(product.id), onSuccess: refresh });
  const analysis = product.photo_analysis_current ? product.image_analysis : null;

  return (
    <div className="card">
      <h3>Product photos</h3>
      <p className="muted small">
        Before writing, the AI studies the photos (colors, shape, visible details) and uses what it sees in the copy.
      </p>
      <ErrorAlert error={upload.error || addUrl.error || remove.error || analyze.error} />
      {product.images.length > 0 && (
        <div className="row" style={{ marginBottom: 10 }}>
          {product.images.map((_, i) => (
            <div key={i} style={{ position: "relative" }}>
              {sources[i] ? (
                <img src={sources[i]} alt={`Product photo ${i + 1}`} style={{ width: 84, height: 84, objectFit: "cover", borderRadius: 8, border: "1px solid var(--border)" }} />
              ) : (
                <div style={{ width: 84, height: 84, borderRadius: 8, background: "var(--surface-2)" }} />
              )}
              {canEdit && (
                <button className="small danger" style={{ position: "absolute", top: 2, right: 2, padding: "0 6px" }}
                  title="Remove" onClick={() => remove.mutate(i)}>×</button>
              )}
            </div>
          ))}
        </div>
      )}
      {product.images.length > 0 && (
        <div className="photo-analysis">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="stat-label"><Icon name="sparkle" size={14} />What the AI sees</span>
            {canEdit && (
              <button className="small" disabled={analyze.isPending} onClick={() => analyze.mutate()}>
                {analyze.isPending ? "Looking at the photos…" : analysis ? "Re-analyze" : "Analyze photos"}
              </button>
            )}
          </div>
          {analysis ? (
            <>
              {analysis.mismatch && (
                <div className="alert warning" style={{ margin: "8px 0" }}>
                  <Icon name="alert" size={16} /><span>{analysis.mismatch}</span>
                </div>
              )}
              <p className="small" style={{ margin: "8px 0" }}>{analysis.summary}</p>
              <div className="row" style={{ gap: 4, marginBottom: 6 }}>
                {analysis.colors.map((c) => <span key={c} className="chip">{c}</span>)}
                {analysis.style && <span className="chip">{analysis.style}</span>}
              </div>
              <ul className="small" style={{ margin: "0 0 6px", paddingLeft: 18 }}>
                {analysis.visible_features.map((f) => <li key={f}>{f}</li>)}
              </ul>
              {analysis.looks_like_materials.length > 0 && (
                <p className="muted small" style={{ margin: 0 }}>
                  Looks like (not confirmed, written only as an appearance): {analysis.looks_like_materials.join(", ")}
                </p>
              )}
            </>
          ) : (
            <p className="muted small" style={{ margin: "6px 0 0" }}>
              Not analysed yet. This happens automatically on the next generation, or click Analyze photos.
            </p>
          )}
        </div>
      )}
      {canEdit && (
        <>
          <input type="file" accept="image/jpeg,image/png,image/webp,image/gif"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
          <div className="row" style={{ marginTop: 8 }}>
            <input style={{ flex: 1 }} placeholder="…or paste an image URL" value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="small" disabled={!url.startsWith("http") || addUrl.isPending} onClick={() => addUrl.mutate()}>Add</button>
          </div>
        </>
      )}
    </div>
  );
}