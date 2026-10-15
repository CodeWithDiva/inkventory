import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Preset } from "../api";
import { can, EngineSelect, ErrorAlert, Loading, pickDefaultEngine, useMe, useOptions } from "../components";

type Form = Omit<Preset, "id" | "is_builtin">;
const EMPTY: Form = { name: "", tone: "professional", length: "medium", channel: "generic", instructions: "", examples: [] };

export default function PresetsPage() {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: options } = useOptions();
  const { data: presets, isLoading } = useQuery({ queryKey: ["presets"], queryFn: api.presets });
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [learning, setLearning] = useState(false);
  const [form, setForm] = useState<Form>(EMPTY);

  const done = () => {
    queryClient.invalidateQueries({ queryKey: ["presets"] });
    setEditing(null);
  };
  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, examples: form.examples.filter((e) => e.trim()) };
      return editing === "new" ? api.createPreset(body) : api.updatePreset(editing as number, body);
    },
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: api.deletePreset, onSuccess: done });

  const open = (preset?: Preset) => {
    setLearning(false);
    setEditing(preset ? preset.id : "new");
    setForm(preset ? {
      name: preset.name, tone: preset.tone, length: preset.length, channel: preset.channel ?? "generic",
      instructions: preset.instructions, examples: preset.examples,
    } : EMPTY);
  };
  const editable = can(me, "edit");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tone &amp; style</h1>
          <p className="muted">Presets control the voice, length, sales channel and brand rules the AI follows.</p>
        </div>
        {editable && (
          <div className="row">
            <button onClick={() => { setEditing(null); setLearning(true); }}>Learn our brand voice</button>
            <button className="primary" onClick={() => open()}>New preset</button>
          </div>
        )}
      </div>
      <ErrorAlert error={save.error || remove.error} />

      {learning && <LearnVoice onDone={() => { setLearning(false); queryClient.invalidateQueries({ queryKey: ["presets"] }); }} />}

      {editing !== null && (
        <div className="card">
          <h2>{editing === "new" ? "New preset" : "Edit preset"}</h2>
          <div className="grid grid-2">
            <div className="field">
              <label>Name</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Our brand voice" />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <div className="field" style={{ flex: 1 }}>
                <label>Tone</label>
                <select value={form.tone} onChange={(e) => setForm({ ...form, tone: e.target.value })}>
                  {Object.keys(options?.tones ?? {}).map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="field" style={{ flex: 1 }}>
                <label>Length</label>
                <select value={form.length} onChange={(e) => setForm({ ...form, length: e.target.value as Form["length"] })}>
                  {Object.entries(options?.lengths ?? {}).map(([k, [a, b]]) => <option key={k} value={k}>{k} ({a}-{b} words)</option>)}
                </select>
              </div>
              <div className="field" style={{ flex: 1 }}>
                <label>Sales channel</label>
                <select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
                  {Object.entries(options?.channels ?? { generic: "Generic" }).map(([k, n]) => <option key={k} value={k}>{n}</option>)}
                </select>
              </div>
            </div>
          </div>
          <p className="muted small">
            {options?.tones[form.tone]}
            {form.channel !== "generic" && " The channel adds that marketplace's listing rules (title length, bullets, banned phrases) and compliance checks."}
          </p>
          <div className="field">
            <label>Brand guidelines (optional)</label>
            <textarea value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })}
              placeholder={"e.g. Always say 'eco-friendly' instead of 'green'. Never use exclamation marks. Address the reader as 'you'."} />
          </div>
          <div className="field">
            <label>Example descriptions in your brand voice (optional, up to 3)</label>
            {[0, 1, 2].map((i) => (
              <textarea key={i} rows={3} style={{ marginBottom: 6 }} value={form.examples[i] ?? ""}
                onChange={(e) => { const ex = [...form.examples]; ex[i] = e.target.value; setForm({ ...form, examples: ex }); }}
                placeholder={`Example ${i + 1}`} />
            ))}
          </div>
          <div className="row">
            <button className="primary" disabled={!form.name.trim() || save.isPending} onClick={() => save.mutate()}>Save</button>
            <button onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      )}

      {isLoading ? <Loading /> : (
        <div className="grid grid-2" style={{ marginTop: 16 }}>
          {presets?.map((p) => (
            <div className="card" key={p.id} style={{ marginTop: 0 }}>
              <div className="row">
                <h3 style={{ margin: 0 }}>{p.name}</h3>
                {p.is_builtin && <span className="badge">built-in</span>}
                <div className="spacer" />
                {editable && !p.is_builtin && (
                  <>
                    <button className="small" onClick={() => open(p)}>Edit</button>
                    <button className="small danger" onClick={() => confirm(`Delete preset "${p.name}"?`) && remove.mutate(p.id)}>Delete</button>
                  </>
                )}
              </div>
              <p className="small" style={{ marginTop: 6 }}>
                <span className="chip">{p.tone}</span><span className="chip">{p.length}</span>
                {p.channel && p.channel !== "generic" && <span className="chip">{options?.channels[p.channel] ?? p.channel}</span>}
              </p>
              <p className="muted small">{options?.tones[p.tone]}</p>
              {p.instructions && <p className="small" style={{ whiteSpace: "pre-line" }}>{p.instructions}</p>}
            </div>
          ))}
        </div>
      )}

      <Glossary editable={editable} />
    </>
  );
}

function LearnVoice({ onDone }: { onDone: () => void }) {
  const { data: options } = useOptions();
  const { data: catalogs } = useQuery({ queryKey: ["catalogs"], queryFn: api.catalogs });
  const [name, setName] = useState("Our brand voice");
  const [text, setText] = useState("");
  const [catalogId, setCatalogId] = useState<number | null>(null);
  const [engine, setEngine] = useState<string | undefined>();
  const [result, setResult] = useState<string | null>(null);
  useEffect(() => { if (engine === undefined) setEngine(pickDefaultEngine(options)); }, [options, engine]);

  const samples = text.split(/\n\s*-{3,}\s*\n|\n{2,}/).map((s) => s.trim()).filter((s) => s.split(/\s+/).length >= 15);
  const learn = useMutation({
    mutationFn: () => api.learnVoice({ name, samples, catalog_id: catalogId, engine: engine ?? null }),
    onSuccess: (r) => setResult(`Created preset "${r.preset.name}" from ${r.samples_used} samples. ${r.summary}`),
  });

  return (
    <div className="card">
      <h2>Learn our brand voice</h2>
      <p className="muted">
        Paste 3–20 product descriptions your team already wrote (separate them with a blank line), or use the existing
        descriptions from an uploaded catalog. The AI writes a style guide and saves it as a new preset.
      </p>
      {result ? (
        <>
          <div className="alert success">{result}</div>
          <button onClick={onDone}>Done</button>
        </>
      ) : (
        <>
          <ErrorAlert error={learn.error} />
          <div className="grid grid-2">
            <div className="field"><label>Preset name</label><input value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div className="field">
              <label>Also use existing descriptions from</label>
              <select value={catalogId ?? ""} onChange={(e) => setCatalogId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">— no catalog —</option>
                {catalogs?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label>Sample descriptions <span className="muted small">({samples.length} detected, 15+ words each)</span></label>
            <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={"First description…\n\nSecond description…\n\nThird description…"} />
          </div>
          <div className="field"><label>AI engine</label><EngineSelect value={engine} onChange={setEngine} /></div>
          <div className="row">
            <button className="primary" disabled={!name.trim() || (samples.length < 3 && !catalogId) || learn.isPending} onClick={() => learn.mutate()}>
              {learn.isPending ? "Analysing…" : "Learn voice"}
            </button>
            <button onClick={onDone}>Cancel</button>
          </div>
        </>
      )}
    </div>
  );
}

function Glossary({ editable }: { editable: boolean }) {
  const queryClient = useQueryClient();
  const { data: terms } = useQuery({ queryKey: ["glossary"], queryFn: api.glossary });
  const [form, setForm] = useState({ term: "", kind: "avoid", replacement: "" });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["glossary"] });
  const add = useMutation({
    mutationFn: () => api.addGlossaryTerm({ ...form, replacement: form.replacement || null }),
    onSuccess: () => { setForm({ term: "", kind: form.kind, replacement: "" }); refresh(); },
  });
  const remove = useMutation({ mutationFn: api.deleteGlossaryTerm, onSuccess: refresh });

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>Glossary</h2>
      <p className="muted">
        Words the AI must avoid (optionally replaced automatically) and terms it must keep exactly, such as brand or
        product-line names. Applies to every generation in your workspace.
      </p>
      <ErrorAlert error={add.error || remove.error} />
      {editable && (
        <div className="row" style={{ marginBottom: 12, alignItems: "flex-end" }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Rule</label>
            <select style={{ width: 170 }} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="avoid">Avoid this word</option>
              <option value="keep">Keep exactly</option>
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, flex: 1 }}>
            <label>Word or phrase</label>
            <input value={form.term} onChange={(e) => setForm({ ...form, term: e.target.value })} placeholder={form.kind === "avoid" ? "e.g. cheap" : "e.g. AquaShield™"} />
          </div>
          {form.kind === "avoid" && (
            <div className="field" style={{ marginBottom: 0, flex: 1 }}>
              <label>Replace with (optional)</label>
              <input value={form.replacement} onChange={(e) => setForm({ ...form, replacement: e.target.value })} placeholder="e.g. affordable" />
            </div>
          )}
          <button className="primary" disabled={!form.term.trim() || add.isPending} onClick={() => add.mutate()}>Add</button>
        </div>
      )}
      {!terms?.length ? (
        <p className="muted small">No glossary terms yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Rule</th><th>Term</th><th>Replacement</th><th /></tr></thead>
            <tbody>
              {terms.map((t) => (
                <tr key={t.id}>
                  <td><span className={`badge ${t.kind === "avoid" ? "rejected" : "approved"}`}>{t.kind === "avoid" ? "avoid" : "keep exactly"}</span></td>
                  <td>{t.term}</td>
                  <td>{t.replacement ?? <span className="muted">—</span>}</td>
                  <td style={{ textAlign: "right" }}>{editable && <button className="small danger" onClick={() => remove.mutate(t.id)}>Remove</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
