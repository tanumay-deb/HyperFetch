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
const GRAPH_SAMPLES = 60;

const view = { login: $("login"), disabled: $("disabled"),
               noPass: $("noPass"), app: $("app") };

let state = "all";                 // status chip
let cat = "All";                   // sidebar category
let query = "";
let timer = null;
let last = [];
const rates = new Map();           // id -> {bytes, at, bps}
const history = [];                // [{down, up}] for the status graph

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
$("list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.closest(".card").dataset.id;
  const act = b.dataset.act;
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

  li.append(chip, head, track, sub, act);
  return { li, chip, chipIcon, name, pct, track, fill, sub, act,
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
  if (c.buttons !== toggle) {
    const out = [];
    if (toggle === "pause") out.push(iconBtn("pause", "i-pause", "Pause"));
    if (toggle === "resume") out.push(iconBtn("resume", "i-play", "Resume"));
    out.push(iconBtn("delete", "i-trash", "Remove from list", "danger"));
    c.act.replaceChildren(...out);
    c.buttons = toggle;
  }
}

/* --------------------------------------------------------------- render */
function render(downloads) {
  const list = $("list");
  const rows = downloads.filter(keep);

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
function drawGraph() {
  const cv = $("graph");
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 120, h = cv.clientHeight || 34;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if (history.length < 2) return;

  // Both channels share one scale, or a trickle of upload would look like a
  // torrent of it next to a saturated download.
  const peak = Math.max(1, ...history.map((s) => Math.max(s.down, s.up)));
  const step = w / (GRAPH_SAMPLES - 1);
  const x0 = w - (history.length - 1) * step;
  const y = (v) => h - 1 - (v / peak) * (h - 3);

  // Down: filled area. It is the number you came to look at.
  g.beginPath();
  g.moveTo(x0, h);
  history.forEach((s, i) => g.lineTo(x0 + i * step, y(s.down)));
  g.lineTo(x0 + (history.length - 1) * step, h);
  g.closePath();
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(52, 211, 153, .40)");
  grad.addColorStop(1, "rgba(52, 211, 153, .02)");
  g.fillStyle = grad;
  g.fill();

  g.beginPath();
  history.forEach((s, i) => (i ? g.lineTo(x0 + i * step, y(s.down))
                              : g.moveTo(x0, y(s.down))));
  g.strokeStyle = "#34d399";
  g.lineWidth = 1.5;
  g.lineJoin = "round";
  g.stroke();

  // Up: a line only, so it reads as the secondary channel.
  g.beginPath();
  history.forEach((s, i) => (i ? g.lineTo(x0 + i * step, y(s.up))
                              : g.moveTo(x0, y(s.up))));
  g.strokeStyle = "#a78bfa";
  g.lineWidth = 1.5;
  g.stroke();
}

function setStatus(downBps, stats) {
  $("downNow").textContent = speed(downBps);
  $("upNow").textContent = speed(stats.upSpeed || 0);
  $("downTotal").textContent = bytes(stats.downloadedTotal || 0);
  $("upTotal").textContent = bytes(stats.uploadedNow || 0);
  if (stats.version) $("version").textContent = "v" + stats.version;

  history.push({ down: downBps, up: stats.upSpeed || 0 });
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
  for (const d of last) {
    const bps = rateFor(d);
    if (d.status === "Downloading") down += bps;
  }
  render(last);
  setStatus(down, st.ok ? st.body : {});
}

boot();
