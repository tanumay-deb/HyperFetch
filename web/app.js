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

const view = { login: $("login"), disabled: $("disabled"),
               noPass: $("noPass"), app: $("app") };
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
  /* Gate on the status HERE, in what gets stored. Returning 0 while storing
     the live rate did nothing, because the callers read the stored value —
     so a paused download kept advertising the speed it had when it stopped. */
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
    /* An unreachable server is exactly what the offline banner is for, and
       fetch reports it by REJECTING rather than by returning a failed
       response — so without this the poll threw on the way to showing it and
       the banner could never appear. status 0 means "never got an answer". */
    return { ok: false, status: 0, body: {} };
  }
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
  /* Three distinct dead ends, and they need different advice: switched off,
     switched on but never given a password, and simply signed out. */
  if (!s.body.enabled) { show("disabled"); return; }
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
    body: JSON.stringify({ username: $("user").value,
                           password: $("pw").value }),
  });
  btn.disabled = false;
  if (r.ok) {
    $("pw").value = "";
    start();
    return;
  }
  if (r.status === 403) { boot(); return; }   // turned off, or password cleared
  err.textContent = r.body.message || (r.status === 0
    ? "Can't reach HyperFetch — is it still running on your PC?"
    : "Could not sign in.");
  err.hidden = false;
});

$("logout").addEventListener("click", async () => {
  clearInterval(timer);
  await api("/api/logout", { method: "POST" });
  rates.clear();
  cards.clear();
  $("list").replaceChildren();   // don't leave the old list sitting behind the form
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
  err.textContent = r.body.message || (r.status === 0
    ? "Lost contact with HyperFetch — is it still running?"
    : "That link was not accepted.");
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
  if (act === "delete") {
    // Removes it from the list only — the server deliberately never deletes
    // the file itself, so this cannot destroy anything.
    if (!confirm("Remove this download from the list?")) return;
  }
  b.disabled = true;
  try {
    await (act === "delete"
      ? api("/api/downloads/" + encodeURIComponent(id), { method: "DELETE" })
      : api("/api/downloads/" + encodeURIComponent(id) + "/" + act, { method: "POST" }));
  } finally {
    /* Cards are reused now, so this button survives the next poll — a request
       that fails would otherwise leave it disabled for good. */
    b.disabled = false;
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

/* Cards are built once and then updated in place, keyed by download id.
 *
 * Rebuilding the whole list each poll read more simply, but it silently killed
 * the progress-bar transition: a CSS transition never runs on an element's
 * first style, and a fresh element every 1.5s is nothing but first styles. It
 * also made a phone repaint every card on every tick to change a percentage. */
const cards = new Map();      // id -> {li, els..., last-written values}

function build(d) {
  const li = document.createElement("li");
  li.className = "card";
  li.dataset.id = d.id;

  const r1 = document.createElement("div");
  r1.className = "row1";
  const name = document.createElement("span");
  name.className = "name";
  const pct = document.createElement("span");
  pct.className = "pct";
  r1.append(name, pct);

  const bar = document.createElement("div");
  bar.className = "bar";
  // setAttribute, not the .role/.aria* IDL properties — those are recent, and
  // this page's whole point is working on whatever phone is to hand.
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", "100");
  const fill = document.createElement("i");
  bar.append(fill);

  const sub = document.createElement("div");
  sub.className = "sub";

  const actions = document.createElement("div");
  actions.className = "actions";

  li.append(r1, bar, sub, actions);
  return { li, name, pct, bar, fill, sub, actions, state: null, bad: null, act: null };
}

/* Which buttons a card shows depends on its state, so that row is the one part
   that is rebuilt — but only when the state actually changes, not every tick. */
function buttons(toggle) {
  const out = [];
  if (toggle) {
    const b = document.createElement("button");
    b.className = "ghost";
    b.dataset.act = toggle;
    b.textContent = toggle === "pause" ? "Pause" : "Resume";
    out.push(b);
  }
  const del = document.createElement("button");
  del.className = "ghost danger";
  del.dataset.act = "delete";
  del.textContent = "Remove";
  out.push(del);
  return out;
}

function update(c, d, bps) {
  const name = d.name || "download";           // textContent: never innerHTML
  if (c.name.textContent !== name) {
    c.name.textContent = name;
    c.name.title = name;
  }

  const pct = Math.max(0, Math.min(100, d.percent));
  const label = pct.toFixed(0) + "%";
  if (c.pct.textContent !== label) {
    c.pct.textContent = label;
    c.bar.setAttribute("aria-valuenow", String(Math.round(pct)));
  }
  c.fill.style.transform = "scaleX(" + pct / 100 + ")";

  if (c.state !== d.status) {
    c.li.dataset.state = d.status;
    c.state = d.status;
  }

  const text = subtitle(d, bps);
  if (c.sub.textContent !== text) c.sub.textContent = text;
  const bad = d.status === "Error";
  if (c.bad !== bad) {
    c.sub.classList.toggle("bad", bad);
    c.bad = bad;
  }

  const running = d.status === "Downloading" || d.status === "Queued";
  const act = running ? "pause"
            : (d.status === "Paused" || d.status === "Error") ? "resume" : "";
  if (c.act !== act) {
    c.actions.replaceChildren(...buttons(act));
    c.act = act;
  }
}

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
     it genuinely changed — which is rare, the server's order is stable. */
  let ordered = list.children.length === wanted.length;
  for (let i = 0; ordered && i < wanted.length; i++) {
    ordered = list.children[i] === wanted[i];
  }
  if (!ordered) list.replaceChildren(...wanted);

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
