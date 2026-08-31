import { useState } from "react";
import { api, type User } from "../api";

type Mode = "in" | "up";

export default function Auth({
  onSignedIn,
  onBack,
}: {
  onSignedIn: (u: User) => void;
  onBack: () => void;
}) {
  const [mode, setMode] = useState<Mode>("in");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      if (mode === "in") {
        const r = await api.signIn(username, password);
        if (r.ok && r.body.user) {
          onSignedIn(r.body.user);
          return;
        }
        setError(
          r.body.message ||
            (r.status === 0 ? "Can't reach HyperFetch." : "Could not sign in."),
        );
      } else {
        const r = await api.signUp(username, email, password, code);
        if (r.ok) {
          /* One reply whether or not the name was free, because usernames are
             the login here. So the honest next step is "go and try". */
          setNotice(r.body.message || "Account ready. Sign in to continue.");
          setMode("in");
          setPassword("");
          return;
        }
        setError(
          r.body.message ||
            (r.status === 0 ? "Can't reach HyperFetch." : "Could not sign up."),
        );
      }
    } finally {
      setBusy(false);
    }
  }

  const signingUp = mode === "up";

  return (
    <div className="auth">
      <form className="card" onSubmit={submit}>
        <span className="mark" aria-hidden="true">⚡</span>
        <h2>{signingUp ? "Create an account" : "Sign in"}</h2>
        <p className="hint">
          {signingUp
            ? "You need an invite code from whoever runs this."
            : "Use the username you signed up with."}
        </p>

        <label>
          <span className="micro">Username</span>
          <input
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>

        {signingUp && (
          <label>
            <span className="micro">Email</span>
            <input
              type="email"
              autoComplete="email"
              autoCapitalize="none"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
        )}

        <label>
          <span className="micro">Password</span>
          <input
            type="password"
            autoComplete={signingUp ? "new-password" : "current-password"}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {signingUp && (
          <label>
            <span className="micro">Invite code</span>
            <input
              type="text"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
        )}

        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Working…" : signingUp ? "Create account" : "Sign in"}
        </button>

        {error && <p className="err" role="alert">{error}</p>}
        {notice && <p className="ok" role="status">{notice}</p>}

        <p className="switcher">
          {signingUp ? "Already have an account? " : "Have an invite code? "}
          <button
            type="button"
            onClick={() => {
              setMode(signingUp ? "in" : "up");
              setError("");
              setNotice("");
            }}
          >
            {signingUp ? "Sign in" : "Create an account"}
          </button>
        </p>
        <p className="switcher">
          <button type="button" onClick={onBack}>Back</button>
        </p>
      </form>
    </div>
  );
}
