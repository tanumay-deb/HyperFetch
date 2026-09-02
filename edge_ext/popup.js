// Same two ports the worker tries, for the same reason: a user on an older
// desktop app is still on 5000, and the popup has to talk to whichever answers
// rather than reporting "app not running" at someone whose app is running.
const PORTS = [21456, 5000];
const LEGACY_PORT = 5000;
let APP = `http://127.0.0.1:${PORTS[0]}`;

/** First origin that answers /ping, or the preferred one if none does. */
function resolveApp() {
  const order = PORTS.map((p) => `http://127.0.0.1:${p}`);
  const tryAt = (i) => {
    if (i >= order.length) return Promise.resolve(order[0]);
    return fetch(`${order[i]}/ping`)
      .then((r) => (r.ok ? order[i] : Promise.reject(new Error("not ok"))))
      .catch(() => tryAt(i + 1));
  };
  return tryAt(0).then((base) => { APP = base; return base; });
}
const statusEl = document.getElementById("status");
const enabledEl = document.getElementById("enabled");
const msgEl = document.getElementById("msg");
const tokenEl = document.getElementById("token");
const saveTokenBtn = document.getElementById("saveToken");
const pairStateEl = document.getElementById("pairState");

// null = we have not reached the app and do not know. Starting at false meant
// the failure path rendered "not required", so a popup that had just said "app
// not running" also claimed pairing was fine. Two contradictory statements,
// one of them invented.
let needsToken = null;

function setStatus(ok) {
  statusEl.innerHTML =
    `<span class="dot ${ok ? "on" : "off"}"></span>${ok ? "connected" : "app not running"}`;
}

function refreshPairState() {
  chrome.storage.local.get({ token: "" }, (v) => {
    const have = !!v.token;
    if (needsToken === null) {
      // Say what is true: the app has not answered, so this is unknown. A
      // saved token is still worth showing, because it is why the user is
      // looking.
      pairStateEl.textContent = have ? "saved — app unreachable" : "unknown";
      pairStateEl.className = "muted";
    } else if (!needsToken) {
      pairStateEl.textContent = "not required";
      pairStateEl.className = "muted";
    } else if (have) {
      pairStateEl.textContent = "paired ✓";
      pairStateEl.className = "paired-ok";
    } else {
      pairStateEl.textContent = "not paired";
      pairStateEl.className = "paired-no";
    }
  });
}

// Auto-pair: fetch the token straight from the app (it only answers this
// extension's id). Falls back silently to manual paste for unpacked/dev loads.
function autoPair() {
  fetch(`${APP}/pair`)
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j && j.token) chrome.storage.local.set({ token: j.token }, refreshPairState);
    })
    .catch(() => {});
}

// Downloads clicked while the app was closed are held by the worker. Opening the
// popup is the moment to say so — and, if the app is back, to drain them. The
// worker only pings at startup, so a worker that is already awake needs a nudge.
const queuedEl = document.getElementById("queued");
const queuedCard = document.getElementById("queuedCard");

function refreshQueued() {
  chrome.runtime.sendMessage({ type: "PENDING_COUNT" }, (res) => {
    void chrome.runtime.lastError;
    const n = (res && res.count) || 0;
    queuedCard.hidden = n === 0;
    queuedEl.textContent = n === 1 ? "1 download" : `${n} downloads`;
  });
}

resolveApp()
  .then((base) => { showBridge(); return fetch(`${base}/ping`); })
  .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
  .then(({ ok, j }) => {
    setStatus(ok);
    if (ok) {
      // app is up — drain the queue, then show what is left
      chrome.runtime.sendMessage({ type: "HYPERFETCH_FLUSH" }, () => {
        void chrome.runtime.lastError;
        setTimeout(refreshQueued, 300);
      });
    } else {
      refreshQueued();
    }
    needsToken = !!(j && j.needsToken);
    refreshPairState();
    if (ok && needsToken) {
      chrome.storage.local.get({ token: "" }, ({ token }) => { if (!token) autoPair(); });
    }
    // mirror the app's "Download button position" setting (Settings → Browser
    // Integration) into storage — the content script repositions live
    if (ok && j && j.badgeCorner) {
      chrome.storage.local.set({ badgeCorner: j.badgeCorner });
    }
  })
  .catch(() => { showBridge(); setStatus(false); refreshPairState(); refreshQueued(); });

chrome.storage.local.get({ enabled: true, token: "" }, (v) => {
  enabledEl.checked = v.enabled;
  if (v.token) tokenEl.placeholder = "•••••••• (saved)";
});

enabledEl.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: enabledEl.checked });
});


// Shows the real extension version (read from the manifest, never hardcoded)
// and the port actually in use.
//
// This has to run after resolveApp(), not at module scope: APP still holds the
// preferred port until the probe comes back, so reading it here printed 21456
// no matter which port answered — and the "update HyperFetch" prompt, the only
// thing that moves anyone off the old port, never appeared at all.
const verEl = document.getElementById("ver");

function showBridge() {
  if (!verEl) return;
  const version = chrome.runtime.getManifest().version;
  verEl.textContent = `bridge ${new URL(APP).host} · v${version}`;
  if (Number(new URL(APP).port) === LEGACY_PORT) {
    verEl.textContent += " — update HyperFetch";
    verEl.style.color = "#fbbf24";
    verEl.title = "This HyperFetch is an older version. Update it from the app "
                + "to move off port 5000, which other programs often take.";
  }
}

showBridge();   // something sensible while the probe is in flight

saveTokenBtn.addEventListener("click", () => {
  const tok = tokenEl.value.trim();
  if (!tok) { msgEl.textContent = "Paste the token from the app first"; return; }
  chrome.storage.local.set({ token: tok }, () => {
    tokenEl.value = "";
    tokenEl.placeholder = "•••••••• (saved)";
    msgEl.textContent = "Token saved";
    refreshPairState();
  });
});

