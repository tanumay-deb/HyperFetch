# HyperFetch — Backlog

Simple running list. Newest first. Keep entries to one line.

## Done
- _(uncommitted)_ — Extension capture: browser downloads (Download buttons) routed to the app via `chrome.downloads.onCreated` (toggle-gated, cancel-after-accept so app-offline falls back to Chrome); magnet/`.torrent` clicks intercepted → app only (no uTorrent). Mirrored to edge_ext; manifest 1.2.0 + `downloads` permission.
- _(uncommitted)_ — yt-dlp engine (`yt_dl.py`): YouTube/Vimeo/etc. delegate to yt-dlp; auto-detect by host + a New Download "Use yt-dlp" toggle; resolves real title/file. Added to requirements + spec.
- _(uncommitted)_ — v2 Queue Manager (🗂 Queues in sidebar): add/delete queues + per-queue concurrency.
- _(uncommitted)_ — Debug logging: Settings → Advanced toggle writes `hyperfetch.log` (task lifecycle, errors, server downloads).
- _(uncommitted)_ — SHA-256 verification: on finish, fetch `<url>.sha256` sidecar, compare, mismatch → Error (Settings → Advanced toggle).
- `7063014` — wired Network/Advanced settings to the engine: proxy, listen-port, disk-cache, pre-allocate, max-connections, "when complete".
- `8ef8282` — v2 settings Network section + sidebar polish (single collapse btn, smooth slide, compact gauge) + close-button prompt.
- `518b759` — **v2 GUI rewrite** (`gui2/`, `python main.py --v2`): widget cards, grouped list, sidebar, tabbed New Download, details drawer, 6-section settings, tray, scheduler, multi-select, shortcuts, toasts.
- `c1372ad` — torrent live progress + real save_path (Open File works) + in-app settings + nav counts + context menu.
- `f5f1f76` — v1.2.1 release (GUI redesign + torrent peer discovery).
- `0f94ba0` — BitTorrent / magnet via aria2c sidecar.
- `59a5a3b` — HLS quality variant picker.
- earlier — ABDM-style UI overhaul, multi-queue + adaptive segments, crash reporter + update check, v1.2.0 release.

## Next — settings that save but don't act yet
- UPnP / NAT-PMP — needs a UPnP library (aria2 has no port-map flag).
- DNS-over-HTTPS — needs a custom resolver (`requests` has no native DoH).

## Bugs
- Responsive layout breaks on window resize (v1; v2 should be better — verify).
- Verify in v2: errored row shows the message (double-click → drawer Logs), Delete works on a selection, Complete popup buttons open + close. (All fixed in v2 — confirm on a real run.)

## Ideas
- Light theme (v2 is dark-only).
- Per-host rules (cookies/segments per host).

## Decided to keep light (not building)
- Auto-update — notify + open releases page (no installer-swap / signing).
- Crash reporter — local JSON dumps only (no networked endpoint).

## Notes
- HTTP/2/3 declined — multiplexing over one connection kills the multi-socket parallelism this app relies on.
- `bin/aria2c.exe` is fetched by `build.ps1` and bundled by the spec.
- Treat every big change as an invite for adversarial review — "tests pass" is necessary, not sufficient.
