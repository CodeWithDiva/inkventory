import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, AppNotification } from "./api";
import { formatDate } from "./components";
import { Icon } from "./icons";

export default function NotificationBell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const count = useQuery({ queryKey: ["unread-count"], queryFn: api.unreadCount, refetchInterval: 30_000 });
  const list = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, enabled: open });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["unread-count"] });
    queryClient.invalidateQueries({ queryKey: ["notifications"] });
  };
  const markRead = useMutation({ mutationFn: (ids?: number[]) => api.markRead(ids), onSuccess: refresh });

  const openItem = (n: AppNotification) => {
    if (!n.read_at) markRead.mutate([n.id]);
    setOpen(false);
    if (n.link) navigate(n.link);
  };
  const unread = count.data?.count ?? 0;

  return (
    <div className="bell">
      <button className="icon-button bell-button" aria-label={`Notifications (${unread} unread)`} onClick={() => setOpen(!open)}>
        <Icon name="bell" size={18} />
        {unread > 0 && <span className="bell-count">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="bell-panel card">
          <div className="row" style={{ marginBottom: 6 }}>
            <b>Notifications</b>
            <div className="spacer" />
            {unread > 0 && <button className="link small" onClick={() => markRead.mutate(undefined)}>Mark all read</button>}
          </div>
          {list.isLoading && <div className="muted small">Loading…</div>}
          {list.data?.length === 0 && <div className="empty" style={{ padding: 24 }}>You're all caught up.</div>}
          {list.data?.map((n) => (
            <button key={n.id} className={`bell-item ${n.read_at ? "" : "unread"}`} onClick={() => openItem(n)}>
              <div style={{ fontWeight: n.read_at ? 400 : 600 }}>{n.title}</div>
              {n.body && <div className="small muted">{n.body.slice(0, 140)}</div>}
              <div className="small muted">{formatDate(n.created_at)}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
