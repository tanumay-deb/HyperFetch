/* HyperFetch web client.
 *
 * Down speed is derived HERE from successive samples of doneBytes; up speed
 * comes from the server, because only aria2 knows it. That split is deliberate
 * — each number comes from whichever side can actually measure it, rather than
 * a third speed tracker that drifts from the desktop's.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 1500;
const GRAPH_SAMPLES = 64;      // same rolling window as gui2.speed_gauge

const view = { login: $("login"), disabled: $("disabled"),
               noPass: $("noPass"), app: $("app") };

let state = "all";                 // status chip
let cat = "All";                   // sidebar category
let query = "";
let sortKey = "Added";             // same five keys as the desktop toolbar
let sortAsc = false;               // and the same default: newest first
let timer = null;
let last = [];
const rates = new Map();           // id -> {bytes, at, bps}
const history = [];                // rolling speed samples for the sidebar graph

/* Category -> icon + colour. Same mapping as gui2/download_card.py, so a file
   is the same colour in both places. */
const CATS = [
  ["All",        "i-all",      null],
  ["Compressed", "i-archive",  "--c-compressed"],
  ["Programs",   "i-program",  "--c-programs"],
  ["Video",      "i-video",    "--c-video"],
  ["Music",      "i-music",    "--c-music"],
  ["Images",     "i-image",    "--c-images"],
  ["Documents",  "i-document", "--c-documents"],
  ["Other",      "i-folder",   "--c-other"],
];
const CAT_BY_NAME = new Map(CATS.map((c) => [c[0], c]));

/* ------------------------------------------------------------ formatting */
function bytes(n) {
  n = Number(n) || 0;
  if (n <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(n < 10 ? 2 : 1)) + " " + u[i];
}

const speed = (bps) => bytes(bps) + "/s";

function eta(sec) {
  if (!isFinite(sec) || sec <= 0) return "";
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m " + Math.round(sec % 60) + "s";
  const h = Math.floor(sec / 3600);
  return h + "h " + Math.round((sec % 3600) / 60) + "m";
}

function rateFor(d) {
  const now = Date.now();
  const prev = rates.get(d.id);
  let bps = 0;
  /* Gated on the status HERE, in what gets stored — the callers read the
     stored value, so gating the return value alone did nothing and a paused
     download kept advertising the speed it had when it stopped. */
  if (d.status === "Downloading" && prev && now > prev.at) {
    const inst = (d.doneBytes - prev.bytes) * 1000 / (now - prev.at);
    bps = prev.bps ? prev.bps * 0.7 + Math.max(0, inst) * 0.3 : Math.max(0, inst);
  }
  rates.set(d.id, { bytes: d.doneBytes, at: now, bps });
  return bps;
}

/* ------------------------------------------------------------ networking */
async function api(path, opts) {
  let r;
  try {
    r = await fetch(path, Object.assign({
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
    }, opts || {}));
  } catch (e) {
    /* An unreachable server is exactly what the offline note is for, and fetch
       reports it by REJECTING rather than returning a failed response — so
       without this the poll threw on the way to showing it. */
    return { ok: false, status: 0, body: {} };
  }
  let body = null;
  try { body = await r.json(); } catch (e) { /* empty body is fine */ }
  return { ok: r.ok, status: r.status, body: body || {} };
}

/* ------------------------------------------------------------------ views */
function show(which) {
  for (const k in view) view[k].hidden = (k !== which);
}

async function boot() {
  const s = await api("/api/session");
  if (!s.ok) { show("login"); return; }
  /* Three dead ends needing three different answers: switched off, switched
     on but never given a password, and simply signed out. */
  if (!s.body.enabled) { show("disabled"); return; }
  if (!s.body.hasPassword) { show("noPass"); return; }
  if (s.body.authed) { start(); } else { show("login"); $("user").focus(); }
}

function start() {
  show("app");
  buildNav();
  tick();
  clearInterval(timer);
  timer = setInterval(tick, POLL_MS);
}

/* -------------------------------------------------------------- sign in */
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("loginBtn"), err = $("loginError");
  err.hidden = true;
  btn.disabled = true;
  const r = await api("/api/login", {
    method: "POST",
    body: JSON.stringify({ username: $("user").value, password: $("pw").value }),
  });
  btn.disabled = false;
  if (r.ok) { $("pw").value = ""; start(); return; }
  if (r.status === 403) { boot(); return; }     // turned off, or password cleared
  err.textContent = r.body.message || (r.status === 0
    ? "Can't reach HyperFetch. Check it is running on your PC."
    : "Could not sign in.");
  err.hidden = false;
});

$("logout").addEventListener("click", async () => {
  clearInterval(timer);
  await api("/api/logout", { method: "POST" });
  rates.clear();
  cards.clear();
  history.length = 0;
  $("list").replaceChildren();
  closeNav();
  show("login");
});

/* ------------------------------------------------------------------- add */
$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("addUrl"), err = $("addError");
  const url = input.value.trim();
  if (!url) return;
  err.hidden = true;
  const r = await api("/api/downloads", {
    method: "POST", body: JSON.stringify({ url }),
  });
  if (r.ok) { input.value = ""; closeNav(); tick(); return; }
  err.textContent = r.body.message || (r.status === 0
    ? "Can't reach HyperFetch."
    : "That link was not accepted.");
  err.hidden = false;
});

/* ---------------------------------------------------------------- drawer */
const openNav = () => { $("side").classList.add("open"); $("scrim").hidden = false; };
const closeNav = () => { $("side").classList.remove("open"); $("scrim").hidden = true; };
$("openNav").addEventListener("click", openNav);
$("closeNav").addEventListener("click", closeNav);
$("scrim").addEventListener("click", closeNav);

/* --------------------------------------------------------------- filters */
function buildNav() {
  const nav = $("nav");
  nav.replaceChildren(...CATS.map(([name, icon, tint]) => {
    const b = document.createElement("button");
    b.className = "nav-row" + (name === cat ? " is-on" : "");
    b.dataset.cat = name;
    if (tint) b.style.setProperty("--tint", `var(${tint})`);

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ic");
    svg.setAttribute("viewBox", "0 0 24 24");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + icon);
    svg.append(use);

    const label = document.createElement("span");
    label.textContent = name === "All" ? "All downloads" : name;
    const tally = document.createElement("span");
    tally.className = "tally";
    b.append(svg, label, tally);
    return b;
  }));
}

$("nav").addEventListener("click", (e) => {
  const b = e.target.closest(".nav-row");
  if (!b) return;
  cat = b.dataset.cat;
  for (const r of $("nav").children) r.classList.toggle("is-on", r === b);
  closeNav();
  render(last);
});

$("chips").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  state = b.dataset.state;
  for (const c of $("chips").children) c.classList.toggle("is-on", c === b);
  render(last);
});

/* ------------------------------------------------------------------ sort */
/* The same five keys as the desktop toolbar, and the same behaviour: picking
   the key you are already on flips the direction. */
const SORTS = {
  Added:    (d) => d.added,
  Name:     (d) => (d.name || "").toLowerCase(),
  Size:     (d) => d.totalBytes,
  Progress: (d) => d.percent,
  Speed:    (d) => rates.get(d.id)?.bps || 0,
};

function buildSortMenu() {
  $("sortMenu").replaceChildren(...Object.keys(SORTS).map((key) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.sort = key;
    b.className = key === sortKey ? "is-on" : "";
    const name = document.createElement("span");
    name.textContent = key;
    b.append(name);
    if (key === sortKey) {
      const dir = document.createElement("span");
      dir.className = "dir";
      dir.textContent = sortAsc ? "▲" : "▼";
      b.append(dir);
    }
    li.append(b);
    return li;
  }));
  $("sortBtn").textContent =
    `Sort: ${sortKey} (${sortAsc ? "▲" : "▼"})`;
}

function toggleSortMenu(open) {
  const m = $("sortMenu");
  const show = open === undefined ? m.hidden : open;
  m.hidden = !show;
  $("sortBtn").setAttribute("aria-expanded", String(show));
}

$("sortBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  buildSortMenu();
  toggleSortMenu();
});

$("sortMenu").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-sort]");
  if (!b) return;
  const key = b.dataset.sort;
  if (key === sortKey) sortAsc = !sortAsc;      // same key again flips it
  else { sortKey = key; sortAsc = false; }
  buildSortMenu();
  toggleSortMenu(false);
  render(last);
});

document.addEventListener("click", () => toggleSortMenu(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") toggleSortMenu(false);
});

function sorted(rows) {
  const pick = SORTS[sortKey] || SORTS.Added;
  /* Copy first: the array is the poll's own list, and sorting it in place
     would reorder what the next diff compares against. */
  return rows.slice().sort((a, b) => {
    const x = pick(a), y = pick(b);
    const c = x < y ? -1 : x > y ? 1 : 0;
    return sortAsc ? c : -c;
  });
}

$("search").addEventListener("input", (e) => {
  query = e.target.value.trim().toLowerCase();
  render(last);
});

function keep(d) {
  if (cat !== "All" && d.category !== cat) return false;
  if (query && !(d.name || "").toLowerCase().includes(query)) return false;
  switch (state) {
    case "active": return d.status === "Downloading" || d.status === "Queued";
    case "paused": return d.status === "Paused";
    case "done": return d.status === "Completed";
    case "failed": return d.status === "Error";
    default: return true;
  }
}

/* --------------------------------------------------------------- actions */
/* Saving is a plain link the browser follows, never fetch(): fetch would pull
   the whole file into memory before the phone saw a byte of it, and these are
   the multi-gigabyte files the PC downloaded on the phone's behalf. The server
   sends Content-Disposition: attachment, so Safari puts it in Files. */
function saveHref(id, index) {
  return "/api/downloads/" + encodeURIComponent(id) + "/file" +
         (index === undefined ? "" : "/" + index);
}

function note(text) {
  const p = document.createElement("p");
  p.className = "file-note";
  p.textContent = text;
  return p;
}

function fileRow(id, f) {
  const a = document.createElement("a");
  a.className = "file";
  a.href = saveHref(id, f.index);
  a.setAttribute("download", f.name);
  const nm = document.createElement("span");
  nm.className = "file-name";
  nm.textContent = f.path || f.name;
  const sz = document.createElement("span");
  sz.className = "file-size num";
  sz.textContent = bytes(f.size);
  a.append(icon("i-save"), nm, sz);
  return a;
}

async function offerFiles(card, id) {
  const panel = card.files;
  if (!panel.hidden) { panel.hidden = true; return; }    // tapped again: close

  const r = await api("/api/downloads/" + encodeURIComponent(id) + "/files");
  const files = (r.body && r.body.files) || [];
  if (!r.ok || !files.length) {
    panel.replaceChildren(note((r.body && r.body.message) ||
                               "The file is no longer on the PC."));
    panel.hidden = false;
    return;
  }
  /* One file is the common case, and a single tap should not turn into a tap,
     a list, and a second tap. */
  if (files.length === 1) {
    window.location.href = saveHref(id, files[0].index);
    return;
  }
  const rows = files.map((f) => fileRow(id, f));
  if (r.body.truncated) {
    rows.push(note("Only the first " + files.length + " files are listed."));
  }
  panel.replaceChildren(...rows);
  panel.hidden = false;
}

$("list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.closest(".card").dataset.id;
  const act = b.dataset.act;
  if (act === "save") {
    b.disabled = true;
    try { await offerFiles(cards.get(id), id); } finally { b.disabled = false; }
    return;
  }
  if (act === "delete" &&
      !confirm("Remove this download from the list? The file itself is kept.")) return;
  b.disabled = true;
  try {
    await (act === "delete"
      ? api("/api/downloads/" + encodeURIComponent(id), { method: "DELETE" })
      : api("/api/downloads/" + encodeURIComponent(id) + "/" + act, { method: "POST" }));
  } finally {
    /* Cards are reused across polls, so this button survives — a request that
       fails would otherwise leave it disabled for good. */
    b.disabled = false;
  }
  tick();
});

/* ---------------------------------------------------------------- cards */
const cards = new Map();           // id -> {li, els..., last-written values}

function icon(name, cls) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls || "ic");
  svg.setAttribute("viewBox", "0 0 24 24");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#" + name);
  svg.append(use);
  return svg;
}

function iconBtn(act, glyph, label, extra) {
  const b = document.createElement("button");
  b.className = "icon-btn" + (extra ? " " + extra : "");
  b.dataset.act = act;
  b.title = label;
  b.setAttribute("aria-label", label);
  /* The word shows at phone widths, where the buttons go full width and a bare
     glyph would be both wasteful of the space and vague about what Remove
     does. Desktop keeps them compact and icon-only, like the app. */
  const word = document.createElement("span");
  word.className = "btn-label";
  word.textContent = label.split(" ")[0];
  b.append(icon(glyph), word);
  return b;
}

function build(d) {
  const li = document.createElement("li");
  li.className = "card";
  li.dataset.id = d.id;

  const chip = document.createElement("div");
  chip.className = "chip-ic";
  const chipIcon = icon("i-folder");
  chip.append(chipIcon);

  const head = document.createElement("div");
  head.className = "head";
  const name = document.createElement("span");
  name.className = "name";
  const pct = document.createElement("span");
  pct.className = "pct num";
  head.append(name, pct);

  const track = document.createElement("div");
  track.className = "bar-track";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  const fill = document.createElement("i");
  track.append(fill);

  const sub = document.createElement("div");
  sub.className = "sub";

  const act = document.createElement("div");
  act.className = "act";

  const files = document.createElement("div");
  files.className = "files";
  files.hidden = true;

  li.append(chip, head, track, sub, act, files);
  return { li, chip, chipIcon, name, pct, track, fill, sub, act, files,
           glyph: null, tint: null, stateName: null, bad: null, buttons: null };
}

function swarm(d) {
  const p = d.peers, s = d.seeds;
  return `${p} peer${p === 1 ? "" : "s"} · ${s} seed${s === 1 ? "" : "s"}`;
}

function subtitle(d, bps) {
  if (d.status === "Error") return d.error || "Failed";
  if (d.verifying) return "Checking files — " + d.verifiedPercent + "%";
  if (d.metaFailed) return "No details yet";
  if (d.fetchingMeta) return "Reading torrent details…";
  if (d.isTorrent && !d.totalBytes) return "Reading torrent details…";
  if (d.seeding) return "Seeding · " + swarm(d) +
                        (d.upSpeed ? " · ↑ " + speed(d.upSpeed) : "");

  const bits = [bytes(d.doneBytes) + " / " + bytes(d.totalBytes)];
  if (bps > 0) bits.push(speed(bps));
  if (d.isTorrent) bits.push(swarm(d));
  if (bps > 0 && d.totalBytes > d.doneBytes) {
    const t = eta((d.totalBytes - d.doneBytes) / bps);
    if (t) bits.push(t + " left");
  }
  return bits.join(" · ");
}

function update(c, d, bps) {
  const name = d.name || "download";      // textContent only, never innerHTML
  if (c.name.textContent !== name) {
    c.name.textContent = name;
    c.name.title = name;
  }

  const glyph = d.isTorrent ? "i-magnet"
              : (CAT_BY_NAME.get(d.category) || CAT_BY_NAME.get("Other"))[1];
  const tint = d.isTorrent ? "--c-compressed"
             : (CAT_BY_NAME.get(d.category) || CAT_BY_NAME.get("Other"))[2];
  if (c.glyph !== glyph) {
    c.chipIcon.firstChild.setAttribute("href", "#" + glyph);
    c.glyph = glyph;
  }
  if (c.tint !== tint) {
    c.chip.style.setProperty("--tint", `var(${tint})`);
    c.tint = tint;
  }

  const pct = Math.max(0, Math.min(100, d.percent));
  const label = pct.toFixed(0) + "%";
  if (c.pct.textContent !== label) {
    c.pct.textContent = label;
    c.track.setAttribute("aria-valuenow", String(Math.round(pct)));
  }
  c.fill.style.transform = "scaleX(" + pct / 100 + ")";

  if (c.stateName !== d.status) {
    c.li.dataset.state = d.status;
    c.stateName = d.status;
  }

  const text = subtitle(d, bps);
  if (c.sub.textContent !== text) c.sub.textContent = text;
  const bad = d.status === "Error";
  if (c.bad !== bad) { c.sub.classList.toggle("bad", bad); c.bad = bad; }

  const running = d.status === "Downloading" || d.status === "Queued";
  const toggle = running ? "pause"
               : (d.status === "Paused" || d.status === "Error") ? "resume" : "";
  /* Only this row is rebuilt, and only when the state it depends on changes. */
  const key = toggle + (d.status === "Completed" ? "+save" : "");
  if (c.buttons !== key) {
    const out = [];
    if (toggle === "pause") out.push(iconBtn("pause", "i-pause", "Pause"));
    if (toggle === "resume") out.push(iconBtn("resume", "i-play", "Resume"));
    if (d.status === "Completed") out.push(iconBtn("save", "i-save", "Save to device"));
    out.push(iconBtn("delete", "i-trash", "Remove from list", "danger"));
    c.act.replaceChildren(...out);
    c.files.replaceChildren();
    c.files.hidden = true;
    c.buttons = key;
  }
}

/* --------------------------------------------------------------- render */
function render(downloads) {
  const list = $("list");
  const rows = sorted(downloads.filter(keep));

  const wanted = rows.map((d) => {
    let c = cards.get(d.id);
    if (!c) cards.set(d.id, (c = build(d)));
    update(c, d, rates.get(d.id)?.bps || 0);
    return c.li;
  });

  const live = new Set(rows.map((d) => d.id));
  for (const [id, c] of cards) {
    if (!live.has(id)) { c.li.remove(); cards.delete(id); }
  }

  /* Re-inserting a node restarts its transitions, so only touch the order when
     it genuinely changed — the server's order is stable. */
  let ordered = list.children.length === wanted.length;
  for (let i = 0; ordered && i < wanted.length; i++) {
    ordered = list.children[i] === wanted[i];
  }
  if (!ordered) list.replaceChildren(...wanted);

  // Counts per category, so the sidebar says how much is behind each row.
  const tally = new Map();
  for (const d of downloads) tally.set(d.category, (tally.get(d.category) || 0) + 1);
  for (const row of $("nav").children) {
    const n = row.dataset.cat === "All" ? downloads.length : (tally.get(row.dataset.cat) || 0);
    const el = row.querySelector(".tally");
    const txt = n ? String(n) : "";
    if (el.textContent !== txt) el.textContent = txt;
  }

  $("heading").textContent = cat === "All" ? "All downloads" : cat;

  const empty = $("empty");
  empty.hidden = rows.length > 0;
  if (!rows.length) {
    empty.textContent = downloads.length
      ? "Nothing here. Try a different filter."
      : "No downloads yet. Paste a link to start one.";
  }
}

/* -------------------------------------------------- status strip + graph */
/* ------------------------------------------- sidebar graph + status strip */
/* A direct port of gui2/speed_gauge.py SpeedGraph, down to the numbers: two
   faint gridlines at thirds, a 5-wide centred moving average, a Catmull-Rom
   spline through the points, the area under it filled with the accent at
   alpha 40/255, and a 2px accent stroke on top. Same window (64 samples) and
   the same rolling-max scale, so the web graph and the desktop one describe
   the same download the same way. */
function movingAvg(values, k) {
  const n = values.length;
  if (k <= 1 || n < 2) return values.slice();
  const half = k >> 1;
  const out = [];
  for (let i = 0; i < n; i++) {
    const lo = Math.max(0, i - half), hi = Math.min(n, i + half + 1);
    let sum = 0;
    for (let j = lo; j < hi; j++) sum += values[j];
    out.push(sum / (hi - lo));
  }
  return out;
}

/* Catmull-Rom through the points, emitted as cubic Beziers — the same
   conversion gui2/graphing.py smooth_path does. */
function splinePath(g, pts) {
  g.moveTo(pts[0].x, pts[0].y);
  const n = pts.length;
  if (n < 3) {
    for (let i = 1; i < n; i++) g.lineTo(pts[i].x, pts[i].y);
    return;
  }
  for (let i = 0; i < n - 1; i++) {
    const p0 = pts[i > 0 ? i - 1 : 0];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2 < n ? i + 2 : n - 1];
    g.bezierCurveTo(p1.x + (p2.x - p0.x) / 6, p1.y + (p2.y - p0.y) / 6,
                    p2.x - (p3.x - p1.x) / 6, p2.y - (p3.y - p1.y) / 6,
                    p2.x, p2.y);
  }
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawGraph() {
  const cv = $("graph");
  if (!cv.clientWidth) return;                 // in the closed drawer
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight || 54;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);

  g.strokeStyle = css("--border");
  g.lineWidth = 1;
  for (let i = 1; i < 3; i++) {
    const y = Math.round(h * i / 3) + 0.5;     // +0.5 keeps a 1px line crisp
    g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
  }

  const n = history.length;
  if (n < 2) return;
  const peak = Math.max(1, ...history);
  const vals = movingAvg(history, 5);
  const pts = vals.map((v, i) => ({
    x: w * i / (n - 1),
    y: h - (v / peak) * (h - 6) - 3,
  }));

  const accent = css("--accent");
  g.beginPath();
  splinePath(g, pts);
  g.save();
  g.lineTo(pts[n - 1].x, h);
  g.lineTo(pts[0].x, h);
  g.closePath();
  g.fillStyle = accent;
  g.globalAlpha = 40 / 255;                    // the QColor alpha, exactly
  g.fill();
  g.restore();

  g.beginPath();
  splinePath(g, pts);
  g.strokeStyle = accent;
  g.lineWidth = 2;
  g.lineCap = "round";
  g.lineJoin = "round";
  g.stroke();
}

function setStatus(downBps, stats, swarm) {
  $("downNow").textContent = speed(downBps);
  $("upNow").textContent = speed(stats.upSpeed || 0);
  $("downTotal").textContent = bytes(stats.downloadedTotal || 0);
  $("upTotal").textContent = bytes(stats.uploadedNow || 0);
  if (stats.version) $("version").textContent = "v" + stats.version;

  // The sidebar card, same two readouts as the desktop sidebar.
  $("speedNow").textContent = speed(downBps);
  if (swarm.torrents) {
    /* Peers alone flatters: 49 connected peers next to 0 B/s is normal in a
       swarm where nobody holds a complete copy, so the seed count goes beside
       it — and the caption has to name the format or "104 / 5" is a riddle. */
    $("connNow").textContent = swarm.peers + " / " + swarm.seeds;
    $("connCap").textContent = "Peers / Seeds";
  } else {
    $("connNow").textContent = String(swarm.active);
    $("connCap").textContent = "Downloading";
  }

  history.push(downBps);
  while (history.length > GRAPH_SAMPLES) history.shift();
  drawGraph();
}

window.addEventListener("resize", drawGraph);

/* ------------------------------------------------------------------ poll */
async function tick() {
  const [dl, st] = await Promise.all([
    api("/api/downloads"),
    api("/api/stats"),
  ]);
  if (dl.status === 401 || dl.status === 403) {   // signed out, or switched off
    clearInterval(timer);
    boot();
    return;
  }
  $("offline").hidden = dl.ok;
  if (!dl.ok) return;

  last = dl.body.downloads || [];
  let down = 0;
  const swarm = { peers: 0, seeds: 0, torrents: 0, active: 0 };
  for (const d of last) {
    const bps = rateFor(d);
    if (d.status === "Downloading") { down += bps; swarm.active++; }
    /* Seeding torrents are left out of the swarm totals: their peers are
       people taking from you, and counting them makes the number describe
       something other than what is being fetched. */
    if (d.isTorrent && !d.seeding && d.status !== "Completed") {
      swarm.torrents++;
      swarm.peers += d.peers;
      swarm.seeds += d.seeds;
    }
  }
  render(last);
  setStatus(down, st.ok ? st.body : {}, swarm);
}

boot();
