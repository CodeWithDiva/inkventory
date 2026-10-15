import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Product } from "./api";
import { can, ErrorAlert, formatDate, useMe } from "./components";

export function useTeam() {
  return useQuery({ queryKey: ["users"], queryFn: api.users, staleTime: 60_000 });
}

export function AssigneeSelect({ product }: { product: Product }) {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: team } = useTeam();
  const assign = useMutation({
    mutationFn: (userId: number | null) => api.assignProduct(product.id, userId),
    onSuccess: (p) => queryClient.setQueryData(["product", product.id], p),
  });
  return (
    <div className="field">
      <label>Assigned to</label>
      <select value={product.assignee_id ?? ""} disabled={!can(me, "edit") || assign.isPending}
        onChange={(e) => assign.mutate(e.target.value ? Number(e.target.value) : null)}>
        <option value="">— nobody —</option>
        {team?.filter((u) => u.is_active).map((u) => <option key={u.id} value={u.id}>{u.full_name} ({u.role})</option>)}
      </select>
      <ErrorAlert error={assign.error} />
    </div>
  );
}

function renderBody(body: string) {
  // Highlight @Name mentions.
  return body.split(/(@[\w.-]+)/g).map((part, i) => (part.startsWith("@") ? <span key={i} className="mention">{part}</span> : part));
}

export function Comments({ productId, language, versionId }: { productId: number; language: string; versionId?: number }) {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: team } = useTeam();
  const { data: comments } = useQuery({ queryKey: ["comments", productId, language], queryFn: () => api.comments(productId, language) });
  const [text, setText] = useState("");
  const [mentions, setMentions] = useState<number[]>([]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["comments", productId] });
  const add = useMutation({
    mutationFn: () => api.addComment(productId, { body: text, language, version_id: versionId ?? null, mentions }),
    onSuccess: () => { setText(""); setMentions([]); refresh(); },
  });
  const resolve = useMutation({ mutationFn: ({ id, resolved }: { id: number; resolved: boolean }) => api.resolveComment(id, resolved), onSuccess: refresh });
  const remove = useMutation({ mutationFn: api.deleteComment, onSuccess: refresh });

  const mention = (id: number, name: string) => {
    if (!mentions.includes(id)) setMentions([...mentions, id]);
    setText((t) => `${t}${t && !t.endsWith(" ") ? " " : ""}@${name.split(" ")[0]} `);
  };
  const others = team?.filter((u) => u.is_active && u.id !== me?.id) ?? [];

  return (
    <div className="card">
      <h3>Comments</h3>
      <ErrorAlert error={add.error || resolve.error || remove.error} />
      {!comments?.length && <p className="muted small">No comments yet. Discuss changes with your team here.</p>}
      {comments?.map((c) => (
        <div key={c.id} className={`comment ${c.resolved ? "resolved" : ""}`}>
          <div className="row small">
            <b>{c.author}</b><span className="muted">{formatDate(c.created_at)}</span>
            <div className="spacer" />
            <button className="link small" onClick={() => resolve.mutate({ id: c.id, resolved: !c.resolved })}>
              {c.resolved ? "Reopen" : "Resolve"}
            </button>
            {(c.user_id === me?.id || can(me, "admin")) && (
              <button className="link small" style={{ color: "var(--danger)" }} onClick={() => remove.mutate(c.id)}>Delete</button>
            )}
          </div>
          <div style={{ whiteSpace: "pre-wrap" }}>{renderBody(c.body)}</div>
        </div>
      ))}
      <div className="field" style={{ marginTop: 10 }}>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Write a comment…" />
      </div>
      {others.length > 0 && (
        <div className="row small" style={{ marginBottom: 8 }}>
          <span className="muted">Mention:</span>
          {others.map((u) => (
            <button key={u.id} className={`small ${mentions.includes(u.id) ? "primary" : ""}`} onClick={() => mention(u.id, u.full_name)}>
              @{u.full_name.split(" ")[0]}
            </button>
          ))}
        </div>
      )}
      <button className="primary" disabled={!text.trim() || add.isPending} onClick={() => add.mutate()}>Comment</button>
    </div>
  );
}
