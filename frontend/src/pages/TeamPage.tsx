import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { can, ErrorAlert, formatDate, Loading, SettingsTabs, useMe } from "../components";

const ROLE_HELP: Record<string, string> = {
  owner: "Full access, created the workspace",
  admin: "Everything, including team and store connections",
  editor: "Upload catalogs, generate and edit descriptions, export",
  reviewer: "Approve or reject descriptions",
};

export default function TeamPage() {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: users, isLoading } = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "editor" });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });
  const create = useMutation({
    mutationFn: () => api.createUser(form),
    onSuccess: () => {
      setForm({ email: "", full_name: "", password: "", role: "editor" });
      refresh();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { role?: string; is_active?: boolean } }) => api.updateUser(id, body),
    onSuccess: refresh,
  });
  const admin = can(me, "admin");

  return (
    <>
      <SettingsTabs />
      <div className="page-head">
        <div>
          <h1>Team</h1>
          <p className="muted">People in {me?.organization.name} and what they can do.</p>
        </div>
      </div>
      <ErrorAlert error={create.error || update.error} />

      {admin && (
        <div className="card">
          <h2>Add team member</h2>
          <div className="grid grid-4">
            <div className="field"><label>Name</label><input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
            <div className="field"><label>Email</label><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
            <div className="field"><label>Temporary password</label><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="min 8 characters" /></div>
            <div className="field">
              <label>Role</label>
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="editor">Editor</option>
                <option value="reviewer">Reviewer</option>
                <option value="admin">Admin</option>
              </select>
            </div>
          </div>
          <p className="muted small">{ROLE_HELP[form.role]}</p>
          <button className="primary" disabled={!form.email || !form.full_name || form.password.length < 8 || create.isPending} onClick={() => create.mutate()}>
            Add member
          </button>
        </div>
      )}

      <div className="card">
        {isLoading ? <Loading /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Joined</th></tr></thead>
              <tbody>
                {users?.map((u) => (
                  <tr key={u.id}>
                    <td><b>{u.full_name}</b>{u.id === me?.id && <span className="muted small"> (you)</span>}</td>
                    <td>{u.email}</td>
                    <td>
                      {admin && u.role !== "owner" ? (
                        <select style={{ width: 130 }} value={u.role} onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })}>
                          <option value="editor">editor</option>
                          <option value="reviewer">reviewer</option>
                          <option value="admin">admin</option>
                        </select>
                      ) : (
                        <span title={ROLE_HELP[u.role]}>{u.role}</span>
                      )}
                    </td>
                    <td>
                      {u.is_active ? <span className="badge approved">active</span> : <span className="badge rejected">deactivated</span>}
                      {admin && u.role !== "owner" && (
                        <button className="link small" style={{ marginLeft: 8 }} onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
                          {u.is_active ? "Deactivate" : "Reactivate"}
                        </button>
                      )}
                    </td>
                    <td className="small">{formatDate(u.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
