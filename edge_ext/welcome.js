// Live app-connection check for the welcome page. Polls /ping (open endpoint)
// and flips the status banner + CTA when the desktop app is found.
const statusEl = document.getElementById("status");
const textEl = document.getElementById("statusText");
const cta = document.getElementById("cta");

function check() {
  fetch("http://127.0.0.1:5000/ping")
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j && j.status === "ok") {
        statusEl.className = "ok";
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
