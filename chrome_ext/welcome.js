// Live app-connection check for the welcome page. Polls /ping (open endpoint)
// and flips the status banner + CTA when the desktop app is found.
const statusEl = document.getElementById("status");
const textEl = document.getElementById("statusText");
const cta = document.getElementById("cta");

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
