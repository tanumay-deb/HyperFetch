const APP = "http://127.0.0.1:5000";
const statusEl = document.getElementById("status");
const enabledEl = document.getElementById("enabled");
const testBtn = document.getElementById("test");
const msgEl = document.getElementById("msg");

function setStatus(ok) {
  statusEl.innerHTML =
    `<span class="dot ${ok ? "on" : "off"}"></span>${ok ? "connected" : "app not running"}`;
  testBtn.disabled = !ok;
}

fetch(`${APP}/ping`)
  .then((r) => setStatus(r.ok))
  .catch(() => setStatus(false));

chrome.storage.local.get({ enabled: true }, (v) => {
  enabledEl.checked = v.enabled;
});

enabledEl.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: enabledEl.checked });
});

testBtn.addEventListener("click", () => {
  fetch(`${APP}/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: "https://proof.ovh.net/files/10Mb.dat",
      filename: "10Mb.dat"
    })
  })
    .then((r) => { msgEl.textContent = r.ok ? "Sent — check the app window" : "App refused the request"; })
    .catch(() => { msgEl.textContent = "Failed — is the app running?"; });
});
