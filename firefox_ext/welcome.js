// Live app-connection check for the welcome page. Polls /ping (open endpoint)
// and flips the status banner + CTA when the desktop app is found.
const statusEl = document.getElementById("status");
const textEl = document.getElementById("statusText");
const cta = document.getElementById("cta");

// Firefox cannot pair itself: each install gets a random extension address, and
// the app's /pair answers only the store listings' ids. It asks the app instead
// (background.js), and this page shows the code the app's question will show.
const ON_FIREFOX = chrome.runtime.getURL("").startsWith("moz-extension:");
const pairTitle = document.getElementById("pairTitle");
const pairText = document.getElementById("pairText");
const PASTE_STEPS =
  "In the app, open Settings → Browser and copy the Browser Pairing Token. " +
  "Then click the HyperFetch icon in your toolbar (it may be under the puzzle-piece " +
  "button), paste the token and press Save.";
if (ON_FIREFOX) {
  pairTitle.textContent = "Approve it once";
  pairText.textContent = "With the app running, HyperFetch asks “Pair Firefox " +
    "with HyperFetch?”. Click Allow if it shows the same code as this page.";
}

function paired() {
  pairTitle.textContent = "Paired ✓";
  pairText.textContent = "This Firefox sends its downloads to HyperFetch.";
}

// Asked on every check while the app is up, until the person answers there.
function askToPair() {
  chrome.storage.local.get({ token: "" }, ({ token }) => {
    if (token) { paired(); return; }
    chrome.runtime.sendMessage({ type: "PAIR_REQUEST" }, (res) => {
      void chrome.runtime.lastError;
      const status = res && res.status;
      if (status === "approved") {
        paired();
      } else if (status === "pending") {
        pairText.textContent = "HyperFetch is asking “Pair Firefox with " +
          `HyperFetch?”. Click Allow if it shows the code ${res.code}.`;
      } else if (status === "denied") {
        pairText.textContent = "Pairing was refused in HyperFetch. It can ask " +
          "again in a few minutes, or pair by hand: " + PASTE_STEPS;
      } else if (status === "unavailable" || status === "unreachable") {
        pairTitle.textContent = "Pair it once";
        pairText.textContent = PASTE_STEPS;   // an app that cannot ask
      }
    });
  });
}

function check() {
  // Both ports, same reason as the worker: an older app answers on 5000.
  const tryPing = (ports) =>
    ports.length === 0
      ? Promise.reject(new Error("no app"))
      : fetch(`http://127.0.0.1:${ports[0]}/ping`)
          .then((r) => (r.ok ? r : Promise.reject(new Error("not ok"))))
          .catch(() => tryPing(ports.slice(1)));
  tryPing([21456, 5000])
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j && j.status === "ok") {
        statusEl.className = "ok";
        if (ON_FIREFOX) askToPair();
        textEl.textContent = "Connected to the HyperFetch app — you're all set!";
        cta.textContent = "Start downloading";
        cta.href = "https://github.com/tanumay-deb/HyperFetch#readme";
      } else {
        throw new Error("bad ping");
      }
    })
    .catch(() => {
      statusEl.className = "down";
      textEl.textContent = "Desktop app not detected — install and launch it, this page updates by itself.";
    });
}
check();
setInterval(check, 3000);
