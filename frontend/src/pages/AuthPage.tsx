import { FormEvent, ReactNode, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, auth } from "../api";
import { ErrorAlert } from "../components";
import { Icon, Logo } from "../icons";
import { AppBackground } from "../Background";

export default function AuthPage({ mode }: { mode: "login" | "register" }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const [form, setForm] = useState({ organization_name: "", full_name: "", email: "", password: "" });
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const providers = useQuery({ queryKey: ["sso-providers"], queryFn: api.ssoProviders, staleTime: Infinity });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  useEffect(() => {
    const ssoError = params.get("sso_error");
    if (ssoError) setError(new Error(ssoError));
  }, [params]);

  const finish = (token: string) => {
    auth.set(token);
    queryClient.clear();
    navigate("/");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mfaToken) {
        finish((await api.verifyMfa(mfaToken, code)).access_token);
      } else if (mode === "login") {
        const result = await api.login(form.email, form.password);
        if (result.mfa_required && result.mfa_token) setMfaToken(result.mfa_token);
        else finish(result.access_token);
      } else {
        finish((await api.register(form)).access_token);
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout>
      <form className="auth-card" onSubmit={submit}>
        {params.get("reset") && <div className="alert success">Your password was changed. Sign in with the new password.</div>}
        <ErrorAlert error={error} />

        {mfaToken ? (
          <>
            <h1>Two-step verification</h1>
            <p className="muted">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
            <div className="field">
              <label>Verification code</label>
              <input autoFocus inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} />
            </div>
            <button className="primary" style={{ width: "100%" }} disabled={busy || code.trim().length < 6}>
              {busy ? "Checking…" : "Verify"}
            </button>
            <p className="muted small" style={{ marginTop: 12, textAlign: "center" }}>
              <button type="button" className="link small" onClick={() => { setMfaToken(null); setCode(""); }}>Back</button>
            </p>
          </>
        ) : (
          <>
            <h1>{mode === "login" ? "Sign in" : "Create your workspace"}</h1>
            <p className="muted" style={{ marginBottom: 24 }}>
              {mode === "login" ? "Welcome back. Sign in to continue." : "Start free. No credit card required."}
            </p>
            {mode === "register" && (
              <>
                <div className="field">
                  <label>Company / store name</label>
                  <input required value={form.organization_name} onChange={set("organization_name")} />
                </div>
                <div className="field">
                  <label>Your name</label>
                  <input required value={form.full_name} onChange={set("full_name")} />
                </div>
              </>
            )}
            <div className="field">
              <label>Email</label>
              <input type="email" required value={form.email} onChange={set("email")} />
            </div>
            <div className="field">
              <label>Password</label>
              <input type="password" required minLength={mode === "register" ? 8 : 1} value={form.password} onChange={set("password")} />
              {mode === "login" && (
                <div className="small" style={{ marginTop: 4, textAlign: "right" }}><Link to="/forgot-password">Forgot password?</Link></div>
              )}
            </div>
            <button className="primary" style={{ width: "100%", height: 40 }} disabled={busy}>
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            </button>
            {!!providers.data?.length && (
              <div className="stack" style={{ gap: 8 }}>
                <div className="auth-divider">or</div>
                {providers.data.map((p) => (
                  <a key={p.id} className="btn" style={{ width: "100%" }} href={`/api/auth/sso/${p.id}/start`}>
                    Continue with {p.name}
                  </a>
                ))}
              </div>
            )}
            <p className="muted small" style={{ marginTop: 12, textAlign: "center" }}>
              {mode === "login" ? (
                <>No account yet? <Link to="/register">Create one</Link></>
              ) : (
                <>Already registered? <Link to="/login">Sign in</Link></>
              )}
            </p>
          </>
        )}
      </form>
    </AuthLayout>
  );
}

const SELLING_POINTS = [
  "Unique, SEO-scored descriptions for thousands of products",
  "15 languages, marketplace rules for Amazon, eBay and Daraz",
  "Review, approval and export to Shopify or WooCommerce",
];

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="auth-page">
      <AppBackground />
      <div className="auth-panel">
        <div style={{ width: "100%", maxWidth: 380 }}>
          <div className="brand" style={{ padding: 0 }}><Logo size={34} /></div>
          {children}
        </div>
      </div>
      <aside className="auth-aside">
        <div className="brand" style={{ padding: 0 }}><Logo size={34} dark /></div>
        <div style={{ position: "relative", zIndex: 1 }}>
          <h2>Product content for your whole catalog, written and reviewed in one place.</h2>
          <ul>
            {SELLING_POINTS.map((point) => (
              <li key={point}><Icon name="check" size={18} />{point}</li>
            ))}
          </ul>
        </div>
        <div className="small" style={{ color: "#64748b", position: "relative", zIndex: 1 }}>© {new Date().getFullYear()} Inkventory</div>
      </aside>
    </div>
  );
}

export function SsoCallbackPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    window.history.replaceState(null, "", "/sso-callback");  // drop the token from the address bar
    if (token) {
      auth.set(token);
      queryClient.clear();
      navigate("/", { replace: true });
    } else {
      navigate("/login?sso_error=Sign-in%20failed", { replace: true });
    }
  }, [navigate, queryClient]);
  return <div className="empty">Signing you in…</div>;
}
