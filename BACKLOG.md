# HyperFetch — Backlog

Simple running list. Newest first. Keep entries to one line.

## Done
- **v2.0.0** — Interactive UI: per-card live speed sparkline + live speed in the window title/tray; floating selection action bar (bulk pause/resume/force/move/remove without right-click — overlay, so it never reflows the list); command palette (Ctrl+K); drag a card out (finished file → Explorer, any card → its URL). UI polish: themed delete-dialog border, selectable + Copy-able drawer logs, smoothed speed graphs (moving-avg + Catmull-Rom; the HLS spikes were a per-segment sampling artifact). Downloads auto-sort into category folders (Video/Music/Images/… by type; Settings toggle, default on; skips torrents).
- _(uncommitted)_ — Polish/stability: safe_filename hardened (Windows reserved names CON/NUL/COM1…, length cap with extension preserved, +tests); byte downloader fails fast on 4xx with actionable messages (403/410 → "right-click → Refresh Address", 401/407 login, 404 moved) and a clear "Connection lost — Resume to retry" on transient give-up; first-run Welcome dialog walks through extension pairing (shown once).
- _(uncommitted)_ — Light theme: `palette.set_theme(dark/light/system)` swaps the active COLORS (light palette added); applied at startup before the UI builds, so it's consistent. Settings → Appearance Light/System now work (toast prompts a restart on change, since widgets bake colours at construction). System detects Windows light/dark.
- _(uncommitted)_ — Card density + animations: tighter card layout (smaller icon, less padding → more rows on screen); new cards fade in; toasts slide-in + fade (and fade out on dismiss).
- _(uncommitted)_ — Module splits: shared `utils.finalize_download()` (cross-volume atomic move) replaces the duplicated finalize in downloader.py + hls.py; `settings.py` page builders extracted to `PageBuilderMixin` in settings_pages.py (568→225 lines). Dialog polish: shared `DialogHeader` + palette design tokens.
- _(uncommitted)_ — Advanced search: `date:` / `ext:` tokens. Per-host rules (Settings → Network → Per-host rules): per host, override the segment count and/or force the yt-dlp engine; matches exact host or any subdomain. `utils.host_rule()` consulted in `Downloader.__init__` (segments, capped by Max Connections) and the engine delegation (ytdlp). Editor dialog + unit tests.
- _(uncommitted)_ — In-app Developer Console (Settings → Advanced → Open Console): live tail of `hyperfetch.log` (incremental by offset), Verbose-debug toggle, Auto-scroll, Copy / Clear / Open-Folder.
- _(uncommitted)_ — Better logging: per-engine child loggers (`hyperfetch.downloader/.hls/.torrent/.ytdlp/.queue/.server`) so each line shows its source; `setup_logging` always captures warnings/errors to `hyperfetch.log` (created lazily) and adds DEBUG verbosity with the toggle; added structured logging to the byte downloader (start/retry/403/429/fail) + torrent/yt-dlp start·done·fail.
- _(uncommitted)_ — Throttle schedule: Settings → Downloads "Scheduled speed limit" (window + limit); enforced each scheduler tick via `SystemMixin._apply_throttle` (overrides the global limit inside the window, reverts outside). Bugs section verified clean in v2.
- _(uncommitted)_ — Empty state: illustration tile + quick actions (New Download · Open Torrent · Open Magnet) + drag hint, wired via `DownloadList.quickAction`. Richer search: `status:`/`category:`/`size:` tokens (`gui2/search.py`, unit-tested) on top of name/URL text.
- `bb7e2e9` — code hygiene: split `app.py` (1011→624) into mixins; dedup engine helpers (`utils.DEFAULT_HEADERS`/`temp_download_path`); palette-ize semantic colours; benefit-first README/store copy.
- `d16875b` — removed the legacy v1 GUI (table-based) + `--v1`; `gui/` is now shared helpers only; dropped "IDM" branding; rename IDM.bat → HyperFetch.bat.
- `2a7b981` — v1.2.2: file sizes in bytes + speed-unit setting; per-queue item view; slim sidebar; Chrome ext store package + promo/screenshot assets.
- `49c91cf` — engine keep-alive `requests.Session` (faster); DoH SNI/recursion fix; multi-queue visibility (badges + counts); HLS logging + settings-driven parallelism; capture allowlist moved into Settings; download History dashboard; yt-dlp quality picker; **v2 GUI is now the default**.
- _(uncommitted)_ — DNS-over-HTTPS (`doh.py`): when on, overrides `socket.getaddrinfo` so in-process HTTP downloads resolve via Cloudflare (1.1.1.1, no recursion); cached, best-effort fallback. Wired to the Network toggle. (Torrents/aria2 unaffected.)
- _(uncommitted)_ — UPnP/NAT-PMP (`upnp.py`): pure-Python SSDP + IGD SOAP opens the torrent listen port (TCP+UDP) on the router; best-effort, threaded. Wired to the Network toggle + Listen Port.
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

## Next (UX & polish — planned)
- Watch Folder: auto-import downloads / `.torrent` files dropped into a monitored folder (IDM parity). [med]
- Empty state follow-ups: Recent URLs list + Watch-Folder shortcut. [low]
- Search follow-up: remember recent searches (dropdown). [low]  _(date: + ext: tokens shipped)_
- Dialog polish: unified `DialogHeader` + design tokens (radius/spacing/margins) in palette; consolidate inline QSS. [med — from code audit]

## Bugs
- _(none open)_ — verified in v2: responsive layout holds at min (940×560) and large (1500×900); errored row shows the message on the card + in the drawer Logs; Delete works on a selection; Complete popup has working buttons.

## Ideas
- DASH (`.mpd`) native support (or lean on yt-dlp).
- Per-thread SOCKS5 proxies (beat per-IP CDN rate limits).
- Inline-on-page quality picker in the extension (near the video, not the panel).

## Decided to keep light (not building)
- Auto-update — notify + open releases page (no installer-swap / signing).
- Crash reporter — local JSON dumps only (no networked endpoint).

## Notes
- HTTP/2/3 declined — multiplexing over one connection kills the multi-socket parallelism this app relies on.
- `bin/aria2c.exe` is fetched by `build.ps1` and bundled by the spec.
- Treat every big change as an invite for adversarial review — "tests pass" is necessary, not sufficient.
