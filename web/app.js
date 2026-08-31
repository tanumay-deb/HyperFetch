/* HyperFetch web UI.
 *
 * Talks to the same /api routes the tests cover. Speed and ETA are derived HERE
 * from successive samples of doneBytes rather than sent by the server, so there
 * is one smoothing implementation on the desktop and one here, instead of a
 * third that drifts from both.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 1500;

const view = { login: $("login"), noPass: $("noPass"), app: $("app") };
let filter = "all";
let timer = null;
const rates = new Map();          // id -> {bytes, at, bps}

/* ------------------------------------------------------------ formatting */
function bytes(n) {
  n = Number(n) || 0;
  if (n <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(2)) + " " + u[i];
}

function speed(bps) {
  return bps > 0 ? bytes(bps) + "/s" : "";
}

function eta(sec) {
  if (!isFinite(sec) || sec <= 0) return "";
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m " + Math.round(sec % 60) + "s";
  const h = Math.floor(sec / 3600);
  return h + "h " + Math.round((sec % 3600) / 60) + "m";
}

/* Smoothed rate per download, from the change in doneBytes between polls. */
function rateFor(d) {
  const now = Date.now();
  const prev = rates.get(d.id);
  let bps = 0;
  if (prev && now > prev.at) {
    const inst = (d.doneBytes - prev.bytes) * 1000 / (now - prev.at);
    bps = prev.bps ? prev.bps * 0.7 + Math.max(0, inst) * 0.3 : Math.max(0, inst);
  }
  rates.set(d.id, { bytes: d.doneBytes, at: now, bps });
  return d.status === "Downloading" ? bps : 0;
}

/* ------------------------------------------------------------ networking */
async function api(path, opts) {
  const r = await fetch(path, Object.assign({
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
  }, opts || {}));
  let body = null;
  try { body = await r.json(); } catch (e) { /* empty body is fine */ }
  return { ok: r.ok, status: r.status, body: body || {} };
}

/* ------------------------------------------------------------ views */
function show(which) {
  for (const k in view) view[k].hidden = (k !== which);
}

async function boot() {
  const s = await api("/api/session");
  if (!s.ok) { show("login"); return; }
  if (!s.body.hasPassword) { show("noPass"); return; }
  if (s.body.authed) { start(); } else { show("login"); $("pw").focus(); }
}

function start() {
  show("app");
  tick();
  clearInterval(timer);
  timer = setInterval(tick, POLL_MS);
}

/* ------------------------------------------------------------ login */
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("loginBtn"), err = $("loginError");
  err.hidden = true;
  btn.disabled = true;
  const r = await api("/api/login", {
    method: "POST",
    body: JSON.stringify({ password: $("pw").value }),
  });
  btn.disabled = false;
  if (r.ok) {
    $("pw").value = "";
    start();
    return;
  }
  err.textContent = r.body.message || "Could not sign in.";
  err.hidden = false;
});

$("logout").addEventListener("click", async () => {
  clearInterval(timer);
  await api("/api/logout", { method: "POST" });
  rates.clear();
  show("login");
});

/* ------------------------------------------------------------ add */
$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("addUrl"), err = $("addError");
  const url = input.value.trim();
  if (!url) return;
  err.hidden = true;
  const r = await api("/api/downloads", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
  if (r.ok) { input.value = ""; tick(); return; }
  err.textContent = r.body.message || "That link was not accepted.";
  err.hidden = false;
});

/* ------------------------------------------------------------ filters */
$("filters").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  filter = b.dataset.filter;
  for (const c of $("filters").children) c.classList.toggle("is-on", c === b);
  render(last);
});

function keep(d) {
  switch (filter) {
    case "active": return d.status === "Downloading" || d.status === "Queued";
    case "paused": return d.status === "Paused";
    case "done": return d.status === "Completed";
    case "failed": return d.status === "Error";
    default: return true;
  }
}

/* ------------------------------------------------------------ actions */
$("list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.closest(".card").dataset.id;
  const act = b.dataset.act;
  b.disabled = true;
  if (act === "delete") {
    // Removes it from the list only — the server deliberately never deletes
    // the file itself, so this cannot destroy anything.
    if (!confirm("Remove this download from the list?")) { b.disabled = false; return; }
    await api("/api/downloads/" + encodeURIComponent(id), { method: "DELETE" });
  } else {
    await api("/api/downloads/" + encodeURIComponent(id) + "/" + act, { method: "POST" });
  }
  tick();
});

/* ------------------------------------------------------------ render */
let last = [];

function swarm(d) {
  const p = d.peers, s = d.seeds;
  return p + (p === 1 ? " peer" : " peers") + " · " +
         s + (s === 1 ? " seed" : " seeds");
}

function subtitle(d, bps) {
  if (d.status === "Error") return d.error || "Failed";
  if (d.verifying) return "Rechecking — " + d.verifiedPercent + "%";
  if (d.metaFailed) return "Couldn't fetch details";
  if (d.fetchingMeta) return "Fetching details…";
  if (d.isTorrent && !d.totalBytes) return "Fetching metadata…";
  if (d.seeding) return "Seeding  ·  " + swarm(d);

  const bits = [bytes(d.doneBytes) + " / " + bytes(d.totalBytes)];
  const s = speed(bps);
  if (s) bits.push(s);
  if (d.isTorrent) bits.push(swarm(d));
  if (bps > 0 && d.totalBytes > d.doneBytes) {
    const t = eta((d.totalBytes - d.doneBytes) / bps);
    if (t) bits.push(t + " left");
  }
  return bits.join("  ·  ");
}

function card(d, bps) {
  const li = document.createElement("li");
  li.className = "card";
  li.dataset.id = d.id;
  li.dataset.state = d.status;

  const r1 = document.createElement("div");
  r1.className = "row1";
  const nm = document.createElement("span");
  nm.className = "name";
  nm.textContent = d.name || "download";       // textContent: never innerHTML
  nm.title = d.name || "";
  const pc = document.createElement("span");
  pc.className = "pct";
  pc.textContent = d.percent.toFixed(0) + "%";
  r1.append(nm, pc);

  const bar = document.createElement("div");
  bar.className = "bar";
  const fill = document.createElement("i");
  fill.style.width = Math.max(0, Math.min(100, d.percent)) + "%";
  bar.append(fill);

  const sub = document.createElement("div");
  sub.className = "sub" + (d.status === "Error" ? " bad" : "");
  sub.textContent = subtitle(d, bps);

  const acts = document.createElement("div");
  acts.className = "actions";
  const running = d.status === "Downloading" || d.status === "Queued";
  if (running || d.status === "Paused" || d.status === "Error") {
    const b = document.createElement("button");
    b.className = "ghost";
    b.dataset.act = running ? "pause" : "resume";
    b.textContent = running ? "Pause" : "Resume";
    acts.append(b);
  }
  const del = document.createElement("button");
  del.className = "ghost danger";
  del.dataset.act = "delete";
  del.textContent = "Remove";
  acts.append(del);

  li.append(r1, bar, sub, acts);
  return li;
}

function render(downloads) {
  const list = $("list");
  const rows = downloads.filter(keep);
  list.replaceChildren(...rows.map((d) => card(d, rates.get(d.id)?.bps || 0)));
  $("empty").hidden = rows.length > 0;

  let active = 0, total = 0;
  for (const d of downloads) {
    if (d.status === "Downloading") { active++; total += rates.get(d.id)?.bps || 0; }
  }
  const rate = speed(total);
  $("totals").textContent = active
    ? active + " active" + (rate ? "  ·  " + rate : "")
    : downloads.length + " download" + (downloads.length === 1 ? "" : "s");
}

async function tick() {
  const r = await api("/api/downloads");
  if (r.status === 401 || r.status === 403) {   // signed out, or password cleared
    clearInterval(timer);
    boot();
    return;
  }
  $("offline").hidden = r.ok;
  if (!r.ok) return;
  last = r.body.downloads || [];
  for (const d of last) rateFor(d);
  render(last);
}

boot();
