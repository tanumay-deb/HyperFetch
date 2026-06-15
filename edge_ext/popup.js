const APP = "http://127.0.0.1:5000";
const statusEl = document.getElementById("status");
const enabledEl = document.getElementById("enabled");
const testBtn = document.getElementById("test");
const msgEl = document.getElementById("msg");
const tokenEl = document.getElementById("token");
const saveTokenBtn = document.getElementById("saveToken");
const pairStateEl = document.getElementById("pairState");

let needsToken = false;

function setStatus(ok) {
  statusEl.innerHTML =
    `<span class="dot ${ok ? "on" : "off"}"></span>${ok ? "connected" : "app not running"}`;
  testBtn.disabled = !ok;
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

fetch(`${APP}/ping`)
  .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
  .then(({ ok, j }) => {
    setStatus(ok);
    needsToken = !!(j && j.needsToken);
    refreshPairState();
  })
  .catch(() => { setStatus(false); refreshPairState(); });

chrome.storage.local.get({ enabled: true, token: "" }, (v) => {
  enabledEl.checked = v.enabled;
  if (v.token) tokenEl.placeholder = "•••••••• (saved)";
});

enabledEl.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: enabledEl.checked });
});

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

testBtn.addEventListener("click", () => {
  chrome.storage.local.get({ token: "" }, (v) => {
    fetch(`${APP}/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-HyperFetch-Token": v.token },
      body: JSON.stringify({
        url: "https://proof.ovh.net/files/10Mb.dat",
        filename: "10Mb.dat",
        token: v.token
      })
    })
      .then((r) => {
        if (r.status === 401) msgEl.textContent = "Not paired — paste the app token above";
        else msgEl.textContent = r.ok ? "Sent — check the app window" : "App refused the request";
      })
      .catch(() => { msgEl.textContent = "Failed — is the app running?"; });
  });
});
