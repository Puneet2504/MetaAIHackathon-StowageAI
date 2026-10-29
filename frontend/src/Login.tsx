import { useEffect, useState } from "react";
import { api, type Session } from "./api";
import Icon from "./Icons";
import { Brand, HeroTanker } from "./Illustrations";

interface Props {
  onSignedIn: (s: Session) => void;
}

const FEATURES = [
  { icon: "order", title: "Reads the voyage order", text: "Paste the charterer's order; the AI turns it into parcels." },
  { icon: "allocate", title: "Plans the stowage", text: "Every parcel placed within cargo lines, segregation and fill limits." },
  { icon: "gauge", title: "Checks stability", text: "Each candidate runs through the deterministic IMO intact stability checks." },
];

// One form: an existing email signs in; a new email is registered after the person confirms it.
export default function Login({ onSignedIn }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [newAccount, setNewAccount] = useState(false);
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demoEmail, setDemoEmail] = useState<string | null>(null);

  useEffect(() => { api.health().then((h) => setDemoEmail(h.demoEmail ?? null)).catch(() => {}); }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newAccount && password !== confirm) { setError("The passwords do not match."); return; }
    setBusy(true);
    try {
      const r = await api.signIn(email, password, newAccount);
      if ("status" in r) { setNewAccount(true); setConfirm(""); return; }
      onSignedIn({ token: r.token, email: r.user.email });
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };

  const changeEmail = (v: string) => { setEmail(v); setError(null); if (newAccount) setNewAccount(false); };

  return (
    <div className="signin">
      <section className="signin-hero" aria-label="About Stowage AI">
        <Brand />
        <h1>Plan a product tanker's cargo in one step</h1>
        <p className="lead">The AI reads the order and the ship's drawings. A deterministic engine does every
          safety-critical number. You approve the plan.</p>
        <ul className="features">
          {FEATURES.map((f) => (
            <li key={f.title}>
              <span className="feature-icon"><Icon name={f.icon} size={22} /></span>
              <span><strong>{f.title}</strong><span className="feature-text">{f.text}</span></span>
            </li>
          ))}
        </ul>
        <HeroTanker />
      </section>

      <section className="signin-panel" aria-labelledby="signin-h">
        <form className="signin-card" onSubmit={submit} noValidate>
          <h2 id="signin-h">{newAccount ? "Create your account" : "Sign in"}</h2>
          <p className="muted small">
            {newAccount
              ? <>There is no account for <strong>{email.trim().toLowerCase()}</strong> yet. Confirm your password to create it.</>
              : "Use your email and password. A new email creates a new account."}
          </p>

          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(e) => changeEmail(e.target.value)}
            autoComplete="email" placeholder="you@company.com" required />

          <label htmlFor="password">Password</label>
          <div className="password-field">
            <input id="password" type={show ? "text" : "password"} value={password}
              onChange={(e) => setPassword(e.target.value)} required minLength={8}
              autoComplete={newAccount ? "new-password" : "current-password"} />
            <button type="button" className="icon-button" onClick={() => setShow((v) => !v)}
              aria-label={show ? "Hide password" : "Show password"}>
              <Icon name={show ? "eyeOff" : "eye"} size={18} />
            </button>
          </div>

          {newAccount && (
            <>
              <label htmlFor="confirm">Confirm password</label>
              <input id="confirm" type={show ? "text" : "password"} value={confirm}
                onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" />
              <p className="muted small">At least 8 characters.</p>
            </>
          )}

          {error && <p className="error" role="alert">{error}</p>}

          <button className="primary wide" type="submit" disabled={busy || !email || !password}>
            {busy ? "Please wait…" : newAccount ? "Create account" : "Sign in"}
          </button>
          {newAccount && (
            <button type="button" className="link" onClick={() => { setNewAccount(false); setError(null); }}>Use a different email</button>
          )}

          {demoEmail && !newAccount && (
            <div className="demo-hint">
              <Icon name="user" size={18} />
              <span>Judging the project? Use the demo account{" "}
                <button type="button" className="link" onClick={() => changeEmail(demoEmail)}>{demoEmail}</button>
                {" "}with the password from the project's README.</span>
            </div>
          )}
          <p className="muted small fineprint">Synthetic demo data only. Not for real loading decisions.</p>
        </form>
      </section>
    </div>
  );
}
