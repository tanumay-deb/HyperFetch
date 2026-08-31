import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Download, type Listing, type User } from "./api";
import Auth from "./components/Auth";
import DownloadCard from "./components/DownloadCard";
import Landing from "./components/Landing";
import QuotaBar from "./components/QuotaBar";

type View = "loading" | "landing" | "auth" | "app" | "closed";

/* Polling rather than SSE or a socket, for a phone-first app: iOS suspends
 * background tabs, which kills a long-lived connection and leaves the UI stale
 * until it reconnects. A poll has no such state — the next one after wake just
 * works. The interval adapts instead, and stops entirely while hidden. */
const FAST = 1500;
const IDLE = 8000;

interface Rate {
  bytes: number;
  at: number;
  bps: number;
}

export default function App() {
  const [view, setView] = useState<View>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [offline, setOffline] = useState(false);
  const [url, setUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState("");

  /* Speed is derived here from successive samples of doneBytes. The server
     does not send it, so there is no third speed tracker to drift. */
  const rates = useRef(new Map<string, Rate>());
  const timer = useRef<number | null>(null);

  const rateFor = useCallback((d: Download): number => {
    const now = Date.now();
    const prev = rates.current.get(d.id);
    let bps = 0;
    if (d.status === "Downloading" && prev && now > prev.at) {
      const inst = ((d.doneBytes - prev.bytes) * 1000) / (now - prev.at);
      bps = prev.bps ? prev.bps * 0.7 + Math.max(0, inst) * 0.3 : Math.max(0, inst);
    }
    rates.current.set(d.id, { bytes: d.doneBytes, at: now, bps });
    return bps;
  }, []);

  const boot = useCallback(async () => {
    const r = await api.session();
    if (!r.ok) {
      setView("landing");
      return;
    }
    if (!r.body.enabled) {
      setView("closed");
      return;
    }
    if (r.body.user) {
      setUser(r.body.user);
      setView("app");
    } else {
      setView("landing");
    }
  }, []);

  const tick = useCallback(async () => {
    const r = await api.list();
    if (r.status === 401 || r.status === 403 || r.status === 503) {
      setUser(null);
      rates.current.clear();
      boot();
      return;
    }
    setOffline(!r.ok);
    if (!r.ok) return;
    for (const d of r.body.downloads) rateFor(d);
    setListing(r.body);
  }, [boot, rateFor]);

  useEffect(() => {
    boot();
  }, [boot]);

  /* One effect owns the polling, so there is exactly one timer no matter how
     the view changes. It slows down when nothing is moving and stops when the
     tab is hidden, which is most of a phone's day. */
  useEffect(() => {
    if (view !== "app") return;

    const busy = (listing?.downloads ?? []).some(
      (d) => d.status === "Downloading" || d.status === "Queued",
    );

    function schedule() {
      if (timer.current !== null) window.clearTimeout(timer.current);
      if (document.hidden) return;
      timer.current = window.setTimeout(async () => {
        await tick();
        schedule();
      }, busy ? FAST : IDLE);
    }

    tick();
    schedule();

    function onVisible() {
      if (document.hidden) {
        if (timer.current !== null) window.clearTimeout(timer.current);
      } else {
        tick();
        schedule();
      }
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, listing?.downloads.some((d) => d.status === "Downloading"), tick]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const link = url.trim();
    if (!link) return;
    setAdding(true);
    setAddError("");
    try {
      const r = await api.add(link);
      if (r.ok) {
        setUrl("");
        tick();
        return;
      }
      setAddError(
        r.body.message ||
          (r.status === 0 ? "Can't reach HyperFetch." : "That link was not accepted."),
      );
    } finally {
      setAdding(false);
    }
  }

  async function signOut() {
    await api.signOut();
    rates.current.clear();
    setUser(null);
    setListing(null);
    setView("landing");
  }

  if (view === "loading") return <div className="note">Loading…</div>;

  if (view === "closed") {
    return (
      <div className="auth">
        <div className="card">
          <span className="mark" aria-hidden="true">⚡</span>
          <h2>Temporarily unavailable</h2>
          <p className="hint">
            HyperFetch is not accepting requests right now. Please try again later.
          </p>
        </div>
      </div>
    );
  }

  if (view === "landing") return <Landing onStart={() => setView("auth")} />;

  if (view === "auth") {
    return (
      <Auth
        onBack={() => setView("landing")}
        onSignedIn={(u) => {
          setUser(u);
          setView("app");
        }}
      />
    );
  }

  const downloads = listing?.downloads ?? [];

  return (
    <div className="app">
      <header className="bar">
        <span className="mark" aria-hidden="true">⚡</span>
        <span className="wordmark">HyperFetch</span>
        <span className="who">{user?.username}</span>
        <button className="ghost" onClick={signOut}>Sign out</button>
      </header>

      <div className="pad">
        <form className="addbar" onSubmit={add}>
          <input
            type="text"
            inputMode="url"
            spellCheck={false}
            placeholder="Paste a magnet or a link"
            aria-label="Magnet or link to download"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <button className="primary" type="submit" disabled={adding}>
            {adding ? "Adding…" : "Add"}
          </button>
        </form>
        {addError && <p className="err" role="alert">{addError}</p>}

        {listing && <QuotaBar used={listing.usedBytes} quota={listing.quotaBytes} />}
      </div>

      {offline && <p className="note warn">Can&rsquo;t reach HyperFetch. Retrying…</p>}

      <ul className="list">
        {downloads.map((d) => (
          <DownloadCard
            key={d.id}
            d={d}
            bps={rates.current.get(d.id)?.bps ?? 0}
            onChanged={tick}
          />
        ))}
      </ul>

      {downloads.length === 0 && !offline && (
        <p className="note">Nothing here yet. Paste a magnet above to start one.</p>
      )}
    </div>
  );
}
