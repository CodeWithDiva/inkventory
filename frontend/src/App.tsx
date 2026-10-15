import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, auth, Me } from "./api";
import { can, Loading, useMe } from "./components";
import { Icon, IconName, Logo } from "./icons";
import AuthPage from "./pages/AuthPage";
import DashboardPage from "./pages/DashboardPage";
import CatalogsPage from "./pages/CatalogsPage";
import UploadPage from "./pages/UploadPage";
import CatalogDetailPage from "./pages/CatalogDetailPage";
import ProductReviewPage from "./pages/ProductReviewPage";
import PresetsPage from "./pages/PresetsPage";
import IntegrationsPage from "./pages/IntegrationsPage";
import TeamPage from "./pages/TeamPage";
import SettingsPage from "./pages/SettingsPage";
import BillingPage from "./pages/BillingPage";
import AdminPage from "./pages/AdminPage";
import DeveloperPage from "./pages/DeveloperPage";
import SecurityPage from "./pages/SecurityPage";
import AuditLogPage from "./pages/AuditLogPage";
import ReportsPage from "./pages/ReportsPage";
import GeneratePage from "./pages/GeneratePage";
import ExportPage from "./pages/ExportPage";
import { ProductsPage, ReviewPage } from "./pages/ProductsPage";
import NotificationBell from "./NotificationBell";
import { AppBackground } from "./Background";
import { SsoCallbackPage } from "./pages/AuthPage";
import { ForgotPasswordPage, ResetPasswordPage } from "./pages/PasswordPages";

type NavItem = { to: string; label: string; icon: IconName; end?: boolean; show?: (me: Me) => boolean; match?: string[] };

// Everyday work stays in view; setup and admin pages sit in a collapsible "More" group.
const NAV: { section?: string; collapsible?: boolean; items: NavItem[] }[] = [
  {
    items: [
      { to: "/", label: "Home", icon: "home", end: true },
      { to: "/catalogs", label: "Catalogs", icon: "folder" },
      { to: "/generate", label: "Generate", icon: "sparkle" },
      { to: "/products", label: "Products", icon: "box" },
      { to: "/review", label: "Review & Approve", icon: "checkCircle" },
      { to: "/export", label: "Export", icon: "send" },
    ],
  },
  {
    section: "More",
    collapsible: true,
    items: [
      { to: "/reports", label: "Insights", icon: "chart" },
      { to: "/presets", label: "Brand voice", icon: "pen" },
      { to: "/integrations", label: "Integrations", icon: "link" },
      { to: "/team", label: "Team", icon: "users" },
      { to: "/billing", label: "Billing", icon: "card" },
      { to: "/settings", label: "Settings", icon: "settings", match: ["/settings", "/security", "/developer", "/audit-log"] },
      { to: "/admin", label: "Platform admin", icon: "building", show: (me) => me.is_superadmin },
    ],
  },
];

const MORE_KEY = "catalogai_nav_more";

function NavGroup({ group, me, pathname }: { group: (typeof NAV)[number]; me: Me; pathname: string }) {
  const items = group.items.filter((item) => !item.show || item.show(me));
  const matches = (item: NavItem) =>
    item.match ? item.match.some((m) => pathname.startsWith(m)) : item.end ? pathname === item.to : pathname.startsWith(item.to);
  const containsActive = items.some(matches);
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(MORE_KEY) === "open";
    } catch {
      return false;
    }
  });
  const expanded = !group.collapsible || open || containsActive;
  const toggle = () => {
    setOpen(!open);
    try {
      localStorage.setItem(MORE_KEY, open ? "closed" : "open");
    } catch {
      /* storage unavailable */
    }
  };
  return (
    <div>
      {group.section && (group.collapsible ? (
        <button className="nav-section nav-toggle" aria-expanded={expanded} onClick={toggle} disabled={containsActive}>
          {group.section}
          <Icon name="chevronDown" size={14} className={expanded ? "rot" : ""} />
        </button>
      ) : <div className="nav-section">{group.section}</div>)}
      {expanded && items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className={`nav-link ${matches(item) ? "active" : ""}`}>
          <Icon name={item.icon} size={18} />{item.label}
        </NavLink>
      ))}
    </div>
  );
}

const TITLES: Record<string, string> = {
  catalogs: "Catalogs", upload: "Upload", products: "Products", reports: "Insights", presets: "Brand voice",
  integrations: "Integrations", developer: "API keys", team: "Team", billing: "Billing", generate: "Generate",
  review: "Review & Approve", export: "Export",
  settings: "Settings", security: "Security", "audit-log": "Audit log", admin: "Platform admin",
};

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
}

function Breadcrumbs() {
  const { pathname } = useLocation();
  const parts = pathname.split("/").filter(Boolean);
  const crumbs: { label: string; to: string }[] = [];
  parts.forEach((part, i) => {
    const to = "/" + parts.slice(0, i + 1).join("/");
    if (/^\d+$/.test(part)) {
      if (parts[i - 1] === "catalogs") crumbs.push({ label: "Catalog", to });
      return;
    }
    if (part === "products" && i > 0) return;  // "/catalogs/1/products/9" -> Catalogs / Catalog / Product
    crumbs.push({ label: TITLES[part] ?? part, to });
  });
  if (parts[parts.length - 2] === "products") crumbs.push({ label: "Product", to: pathname });
  if (crumbs.length < 2) return null;  // top-level pages: the page title says where you are
  return (
    <nav className="breadcrumbs crumb-bar" aria-label="Breadcrumb">
      {crumbs.map((c, i) => (
        <span key={c.to} className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
          {i > 0 && <Icon name="chevronRight" size={14} />}
          {i === crumbs.length - 1 ? <span className="current">{c.label}</span> : <Link to={c.to}>{c.label}</Link>}
        </span>
      ))}
    </nav>
  );
}

function UserMenu({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <div className="menu" ref={ref}>
      <button className="user-button" aria-label="Account menu" onClick={() => setOpen(!open)}>
        <span className="avatar dark">{initials(me.full_name)}</span>
        <span className="user-button-text">
          <span className="user-name">{me.full_name}</span>
          <span className="user-role">{me.role}</span>
        </span>
        <Icon name="chevronDown" size={16} />
      </button>
      {open && (
        <div className="menu-panel" onClick={() => setOpen(false)}>
          <div className="menu-header">
            <div style={{ fontWeight: 600 }}>{me.full_name}</div>
            <div className="muted small">{me.email}</div>
          </div>
          <Link className="menu-item" to="/security"><Icon name="shield" size={16} />Security</Link>
          <Link className="menu-item" to="/settings"><Icon name="settings" size={16} />Settings</Link>
          <Link className="menu-item" to="/billing"><Icon name="card" size={16} />Plan & billing</Link>
          <div className="divider" style={{ margin: "6px 0" }} />
          <button className="menu-item" onClick={onLogout}><Icon name="logout" size={16} />Sign out</button>
        </div>
      )}
    </div>
  );
}

/** Shown instead of an endless spinner when the API can't be reached. */
function ServerDown({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  return (
    <div className="server-down">
      <AppBackground />
      <div className="card">
        <Logo size={36} />
        <h1 style={{ margin: "18px 0 6px" }}>Can't reach the Inkventory server</h1>
        <p className="muted">
          The website is open, but the API it talks to is not answering. If you run Inkventory on this computer,
          make sure the <b>Inkventory API</b> window is open, or start the app again from the <b>Inkventory</b>
          desktop shortcut.
        </p>
        <div className="row" style={{ marginTop: 16 }}>
          <button className="primary" disabled={retrying} onClick={onRetry}>{retrying ? "Trying…" : "Try again"}</button>
          <button onClick={() => { auth.set(null); window.location.href = "/login"; }}>Go to sign in</button>
        </div>
      </div>
    </div>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const { data: me, isLoading, error, refetch, isFetching } = useMe();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  if (!auth.token) return <Navigate to="/login" replace />;
  if (error && !me) return <ServerDown onRetry={() => refetch()} retrying={isFetching} />;
  if (isLoading || !me) return <Loading />;

  const logout = async () => {
    await api.logout().catch(() => undefined);
    auth.set(null);
    queryClient.clear();
    navigate("/login");
  };

  return (
    <div className="app">
      <AppBackground />
      <aside className="sidebar">
        <Link to="/" className="brand" aria-label="Inkventory home">
          <Logo size={32} dark />
        </Link>
        <nav className="nav">
          {NAV.map((group, gi) => <NavGroup key={gi} group={group} me={me} pathname={location.pathname} />)}
        </nav>
        <PlanCard me={me} />
      </aside>
      <div className="content">
        <header className="topbar">
          <TopSearch />
          <div className="spacer" />
          <NotificationBell />
          <UserMenu me={me} onLogout={logout} />
        </header>
        <main className="main">
          <Breadcrumbs />
          {me.must_setup_2fa && (
            <div className="alert warning">
              <Icon name="shield" size={16} />
              <span>Your workspace requires two-step login. <NavLink to="/security">Set it up now</NavLink></span>
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  );
}

function PlanCard({ me }: { me: Me }) {
  const { data } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, staleTime: 30000 });
  const used = data?.usage.this_month ?? 0;
  const limit = data?.usage.limit ?? me.organization.monthly_generation_limit;
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <div className="plan-card">
      <div className="plan-card-head">
        <Icon name="layers" size={16} />
        <span style={{ textTransform: "capitalize" }}>{me.organization.plan} plan</span>
      </div>
      <div className="plan-card-usage">{used.toLocaleString()} / {limit.toLocaleString()} descriptions</div>
      <div className="plan-meter"><div style={{ width: `${pct}%` }} /></div>
      <Link to="/billing" className="btn plan-button">{me.organization.plan === "business" ? "Manage plan" : "Upgrade plan"}</Link>
    </div>
  );
}

function TopSearch() {
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  return (
    <form className="top-search" role="search" onSubmit={(e) => {
      e.preventDefault();
      navigate(`/products?search=${encodeURIComponent(value.trim())}`);
    }}>
      <Icon name="search" size={16} />
      <input placeholder="Search products by name or SKU…" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Search products" />
    </form>
  );
}

function RequireRole({ action, children }: { action: string; children: ReactNode }) {
  const { data: me } = useMe();
  if (!can(me, action)) return <div className="alert warning">You do not have access to this page.</div>;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/sso-callback" element={<SsoCallbackPage />} />
      <Route path="/" element={<Shell><DashboardPage /></Shell>} />
      <Route path="/catalogs" element={<Shell><CatalogsPage /></Shell>} />
      <Route path="/catalogs/upload" element={<Shell><RequireRole action="edit"><UploadPage /></RequireRole></Shell>} />
      <Route path="/catalogs/:catalogId" element={<Shell><CatalogDetailPage /></Shell>} />
      <Route path="/catalogs/:catalogId/products/:productId" element={<Shell><ProductReviewPage /></Shell>} />
      <Route path="/generate" element={<Shell><GeneratePage /></Shell>} />
      <Route path="/products" element={<Shell><ProductsPage /></Shell>} />
      <Route path="/review" element={<Shell><ReviewPage /></Shell>} />
      <Route path="/export" element={<Shell><ExportPage /></Shell>} />
      <Route path="/presets" element={<Shell><PresetsPage /></Shell>} />
      <Route path="/integrations" element={<Shell><IntegrationsPage /></Shell>} />
      <Route path="/team" element={<Shell><TeamPage /></Shell>} />
      <Route path="/billing" element={<Shell><BillingPage /></Shell>} />
      <Route path="/settings" element={<Shell><SettingsPage /></Shell>} />
      <Route path="/admin" element={<Shell><AdminPage /></Shell>} />
      <Route path="/developer" element={<Shell><DeveloperPage /></Shell>} />
      <Route path="/security" element={<Shell><SecurityPage /></Shell>} />
      <Route path="/audit-log" element={<Shell><AuditLogPage /></Shell>} />
      <Route path="/reports" element={<Shell><ReportsPage /></Shell>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
