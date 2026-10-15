import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, auth } from "../api";
import { ErrorAlert } from "../components";
import { AuthLayout } from "./AuthPage";

export function ForgotPasswordPage() {
  const [emailAddr, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSent((await api.forgotPassword(emailAddr)).message);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout>
      <form className="auth-card" onSubmit={submit}>
        <h1>Forgot password</h1>
        <p className="muted">Enter your account email and we'll send you a link to choose a new password.</p>
        <ErrorAlert error={error} />
        {sent ? (
          <div className="alert success">{sent}</div>
        ) : (
          <>
            <div className="field">
              <label>Email</label>
              <input type="email" required value={emailAddr} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <button className="primary" style={{ width: "100%" }} disabled={busy}>
              {busy ? "Sending…" : "Send reset link"}
            </button>
          </>
        )}
        <p className="muted small" style={{ marginTop: 12, textAlign: "center" }}>
          <Link to="/login">Back to sign in</Link>
        </p>
      </form>
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const token = params.get("token") ?? "";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) return setError(new Error("The passwords do not match"));
    setBusy(true);
    setError(null);
    try {
      const result = await api.resetPassword(token, password);
      queryClient.clear();
      if (result.mfa_required) {
        navigate("/login?reset=1");  // accounts with two-step login sign in again with their code
      } else {
        auth.set(result.access_token);
        navigate("/");
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
        <h1>Choose a new password</h1>
        <ErrorAlert error={error} />
        {!token && <div className="alert error">This link is incomplete. Request a new one.</div>}
        <div className="field">
          <label>New password</label>
          <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <div className="field">
          <label>Repeat new password</label>
          <input type="password" required minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        <button className="primary" style={{ width: "100%" }} disabled={busy || !token}>
          {busy ? "Saving…" : "Save password and sign in"}
        </button>
        <p className="muted small" style={{ marginTop: 12, textAlign: "center" }}>
          <Link to="/forgot-password">Request a new link</Link>
        </p>
      </form>
    </AuthLayout>
  );
}
