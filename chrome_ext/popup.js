const APP = "http://127.0.0.1:5000";
const statusEl = document.getElementById("status");
const enabledEl = document.getElementById("enabled");
const msgEl = document.getElementById("msg");
const tokenEl = document.getElementById("token");
const saveTokenBtn = document.getElementById("saveToken");
const pairStateEl = document.getElementById("pairState");

let needsToken = false;

function setStatus(ok) {
  statusEl.innerHTML =
    `<span class="dot ${ok ? "on" : "off"}"></span>${ok ? "connected" : "app not running"}`;
}

function refreshPairState() {
  chrome.storage.local.get({ token: "" }, (v) => {
    const have = !!v.token;
    if (!needsToken) {
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

fetch(`${APP}/ping`)
  .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
  .then(({ ok, j }) => {
    setStatus(ok);
    needsToken = !!(j && j.needsToken);
    refreshPairState();
    if (ok && needsToken) {
      chrome.storage.local.get({ token: "" }, ({ token }) => { if (!token) autoPair(); });
    }
  })
  .catch(() => { setStatus(false); refreshPairState(); });

chrome.storage.local.get({ enabled: true, token: "" }, (v) => {
  enabledEl.checked = v.enabled;
  if (v.token) tokenEl.placeholder = "•••••••• (saved)";
});

enabledEl.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: enabledEl.checked });
});

// show the real extension version (read from the manifest, never hardcoded)
const verEl = document.getElementById("ver");
if (verEl) verEl.textContent = `bridge 127.0.0.1:5000 · v${chrome.runtime.getManifest().version}`;

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

