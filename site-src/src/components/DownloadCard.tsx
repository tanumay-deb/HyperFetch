import { useState } from "react";
import { api, bytes, eta, fileHref, speed, type Download, type FileEntry } from "../api";

/* Same mapping as gui2/download_card.py, so a file is the same colour here as
   it is in the desktop window. */
const CATEGORY: Record<string, { tint: string; glyph: string }> = {
  mkv: { tint: "#ff80ab", glyph: "▶" },
  mp4: { tint: "#ff80ab", glyph: "▶" },
  avi: { tint: "#ff80ab", glyph: "▶" },
  mp3: { tint: "#ff8a80", glyph: "♪" },
  flac: { tint: "#ff8a80", glyph: "♪" },
  zip: { tint: "#b388ff", glyph: "⛁" },
  rar: { tint: "#b388ff", glyph: "⛁" },
  "7z": { tint: "#b388ff", glyph: "⛁" },
  iso: { tint: "#b5b5b5", glyph: "◈" },
  exe: { tint: "#82b1ff", glyph: "⚙" },
  msi: { tint: "#82b1ff", glyph: "⚙" },
  pdf: { tint: "#80d8ff", glyph: "▤" },
  png: { tint: "#4dd0e1", glyph: "▣" },
  jpg: { tint: "#4dd0e1", glyph: "▣" },
};
const TORRENT = { tint: "#b388ff", glyph: "⬡" };
const OTHER = { tint: "#b5b5b5", glyph: "▢" };

function look(d: Download) {
  if (d.isTorrent) return TORRENT;
  const ext = (d.name.split(".").pop() || "").toLowerCase();
  return CATEGORY[ext] || OTHER;
}

function swarm(d: Download) {
  return `${d.peers} peer${d.peers === 1 ? "" : "s"} · ${d.seeds} seed${d.seeds === 1 ? "" : "s"}`;
}

function subtitle(d: Download, bps: number) {
  if (d.status === "Error") return d.error || "Failed";
  /* Finished comes first. A completed torrent whose metadata was never
     recorded has totalBytes 0, and the check below would tell someone it is
     still reading details about a file they can already download. */
  if (d.status === "Completed" && !d.seeding) {
    return d.totalBytes ? `${bytes(d.totalBytes)} · ready` : "Ready";
  }
  if (d.metaFailed) return "No details yet";
  if (d.fetchingMeta || (d.isTorrent && !d.totalBytes)) return "Reading torrent details…";
  if (d.seeding) return `Seeding · ${swarm(d)}`;

  const bits = [`${bytes(d.doneBytes)} / ${bytes(d.totalBytes)}`];
  if (bps > 0) bits.push(speed(bps));
  if (d.isTorrent) bits.push(swarm(d));
  if (bps > 0 && d.totalBytes > d.doneBytes) {
    const left = eta((d.totalBytes - d.doneBytes) / bps);
    if (left) bits.push(`${left} left`);
  }
  return bits.join(" · ");
}

export default function DownloadCard({
  d,
  bps,
  onChanged,
}: {
  d: Download;
  bps: number;
  onChanged: () => void;
}) {
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const { tint, glyph } = look(d);
  const running = d.status === "Downloading" || d.status === "Queued";
  const done = d.status === "Completed";
  /* A finished download is 100% even when nothing recorded its size, which is
     the case for a torrent that completed before its metadata was stored. */
  const pct = done ? 100 : Math.max(0, Math.min(100, d.percent));

  async function act(fn: () => Promise<{ ok: boolean; body: { message?: string } }>) {
    setBusy(true);
    setProblem("");
    try {
      const r = await fn();
      if (!r.ok) setProblem(r.body.message || "That did not work.");
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  async function save() {
    if (files) {
      setFiles(null);
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      const r = await api.files(d.id);
      const list = r.body.files || [];
      if (!r.ok || list.length === 0) {
        setProblem(r.body.message || "That file is no longer here.");
        return;
      }
      /* One file is the common case, and a single tap should not become a tap,
         a list, and a second tap. */
      if (list.length === 1) {
        window.location.href = fileHref(d.id, list[0].index);
        return;
      }
      setFiles(list);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="dl" data-state={d.status}>
      <div className="chip-ic" style={{ ["--tint" as string]: tint }} aria-hidden="true">
        {glyph}
      </div>

      <div className="head">
        <span className="name" title={d.name}>{d.name || "download"}</span>
        <span className="pct num">{pct.toFixed(0)}%</span>
      </div>

      <div
        className="bar-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
      >
        <i style={{ transform: `scaleX(${pct / 100})` }} />
      </div>

      <div className={`sub${d.status === "Error" ? " bad" : ""}`}>
        {problem || subtitle(d, bps)}
        {done && d.expiresInDays !== null && (
          <span className="expiry">
            {" · "}
            {d.expiresInDays === 0 ? "removed today" : `${d.expiresInDays}d left`}
          </span>
        )}
      </div>

      <div className="act">
        {running && (
          <button className="ghost" disabled={busy} onClick={() => act(() => api.pause(d.id))}>
            Pause
          </button>
        )}
        {(d.status === "Paused" || d.status === "Error") && (
          <button className="ghost" disabled={busy} onClick={() => act(() => api.resume(d.id))}>
            Resume
          </button>
        )}
        {done && (
          <button className="ghost" disabled={busy} onClick={save}>
            {files ? "Hide files" : "Save"}
          </button>
        )}
        <button
          className="ghost danger"
          disabled={busy}
          onClick={() => {
            /* Unlike the desktop app this deletes the file, so it says so. */
            if (confirm(`Delete ${d.name}? The file is removed from the machine.`)) {
              act(() => api.remove(d.id));
            }
          }}
        >
          Delete
        </button>
      </div>

      {files && (
        <div className="files">
          {files.map((f) => (
            /* A real link, never fetch(): fetch would pull a multi-gigabyte
               file into memory before the phone saw a byte of it. */
            <a key={f.index} className="file" href={fileHref(d.id, f.index)} download={f.name}>
              <span className="file-name">{f.path || f.name}</span>
              <span className="file-size num">{bytes(f.size)}</span>
            </a>
          ))}
        </div>
      )}
    </li>
  );
}
