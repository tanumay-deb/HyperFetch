# HyperFetch — Backlog

Simple running list. Newest first. Keep entries to one line.

## Done
- **v2.4 – 2.8.9** — see the GitHub releases (from 2.8.9 the notes are `notes/<version>.md`) and CLAUDE.md, which records each change and what was measured.
- **v2.3.x** — Torrent engine and queue hardening, mostly driven by real logs and live-daemon measurement: shared aria2 daemon no longer kills healthy engines or orphans payloads; app exit 5s → 0.001s; concurrency setting actually honoured; per-file progress read from `getFiles` instead of file size on disk; stall-yield so a dead swarm stops blocking the queue; free-space guard; preview playback + Play action; opt-in seeding with an upload cap; shut down / sleep when the queue finishes; duplicates matched by infohash; Force Recheck for torrents; Queue Manager rebuilt; ETA column; file-list sorting; `file://` `.torrent` paths; hash-check progress; **`--file-allocation=falloc` instead of `prealloc`** (prealloc wrote zero bytes across multi-GB files and blocked aria2's single thread for up to 9.5s — the "10 Mb/s then 0" sawtooth).
- **v2.0.0** — Interactive UI: per-card live speed sparkline + live speed in the window title/tray; floating selection action bar (bulk pause/resume/force/move/remove without right-click — overlay, so it never reflows the list); command palette (Ctrl+K); drag a card out (finished file → Explorer, any card → its URL). UI polish: themed delete-dialog border, selectable + Copy-able drawer logs, smoothed speed graphs (moving-avg + Catmull-Rom; the HLS spikes were a per-segment sampling artifact). Downloads auto-sort into category folders (Video/Music/Images/… by type; Settings toggle, default on; skips torrents).
- earlier v2 — Polish/stability: safe_filename hardened (Windows reserved names CON/NUL/COM1…, length cap with extension preserved, +tests); byte downloader fails fast on 4xx with actionable messages (403/410 → "right-click → Refresh Address", 401/407 login, 404 moved) and a clear "Connection lost — Resume to retry" on transient give-up; first-run Welcome dialog walks through extension pairing (shown once).
- earlier v2 — Light theme: `palette.set_theme(dark/light/system)` swaps the active COLORS (light palette added); applied at startup before the UI builds, so it's consistent. Settings → Appearance Light/System now work (it asked for a restart then; since 2026-10-30 a theme change rebuilds the window in place, `_reskin`). System detects Windows light/dark.
- earlier v2 — Card density + animations: tighter card layout (smaller icon, less padding → more rows on screen); new cards fade in; toasts slide-in + fade (and fade out on dismiss).
- earlier v2 — Module splits: shared `utils.finalize_download()` (cross-volume atomic move) replaces the duplicated finalize in downloader.py + hls.py; `settings.py` page builders extracted to `PageBuilderMixin` in settings_pages.py (568→225 lines). Dialog polish: shared `DialogHeader` + palette design tokens.
- earlier v2 — Advanced search: `date:` / `ext:` tokens. Per-host rules (Settings → Network → Per-host rules): per host, override the segment count and/or force the yt-dlp engine; matches exact host or any subdomain. `utils.host_rule()` consulted in `Downloader.__init__` (segments, capped by Max Connections) and the engine delegation (ytdlp). Editor dialog + unit tests.
- earlier v2 — In-app Developer Console (Settings → Advanced → Open Console): live tail of `hyperfetch.log` (incremental by offset), Verbose-debug toggle, Auto-scroll, Copy / Clear / Open-Folder.
- earlier v2 — Better logging: per-engine child loggers (`hyperfetch.downloader/.hls/.torrent/.ytdlp/.queue/.server`) so each line shows its source; `setup_logging` always captures warnings/errors to `hyperfetch.log` (created lazily) and adds DEBUG verbosity with the toggle; added structured logging to the byte downloader (start/retry/403/429/fail) + torrent/yt-dlp start·done·fail.
- earlier v2 — Throttle schedule: Settings → Downloads "Scheduled speed limit" (window + limit); enforced each scheduler tick via `SystemMixin._apply_throttle` (overrides the global limit inside the window, reverts outside). Bugs section verified clean in v2.
- earlier v2 — Empty state: illustration tile + quick actions (New Download · Open Torrent · Open Magnet) + drag hint, wired via `DownloadList.quickAction`. Richer search: `status:`/`category:`/`size:` tokens (`gui2/search.py`, unit-tested) on top of name/URL text.
- `bb7e2e9` — code hygiene: split `app.py` (1011→624) into mixins; dedup engine helpers (`utils.DEFAULT_HEADERS`/`temp_download_path`); palette-ize semantic colours; benefit-first README/store copy.
- `d16875b` — removed the legacy v1 GUI (table-based) + `--v1`; `gui/` is now shared helpers only; dropped "IDM" branding; rename IDM.bat → HyperFetch.bat.
- `2a7b981` — v1.2.2: file sizes in bytes + speed-unit setting; per-queue item view; slim sidebar; Chrome ext store package + promo/screenshot assets.
- `49c91cf` — engine keep-alive `requests.Session` (faster); DoH SNI/recursion fix; multi-queue visibility (badges + counts); HLS logging + settings-driven parallelism; capture allowlist moved into Settings; download History dashboard; yt-dlp quality picker; **v2 GUI is now the default**.
- earlier v2 — DNS-over-HTTPS (`doh.py`): when on, overrides `socket.getaddrinfo` so in-process HTTP downloads resolve via Cloudflare (1.1.1.1, no recursion); cached, best-effort fallback. Wired to the Network toggle. (Torrents/aria2 unaffected.)
- earlier v2 — UPnP/NAT-PMP (`upnp.py`): pure-Python SSDP + IGD SOAP opens the torrent listen port (TCP+UDP) on the router; best-effort, threaded. Wired to the Network toggle + Listen Port.
- earlier v2 — Extension capture: browser downloads (Download buttons) routed to the app via `chrome.downloads.onCreated` (toggle-gated, cancel-after-accept so app-offline falls back to Chrome); magnet/`.torrent` clicks intercepted → app only (no uTorrent). Mirrored to edge_ext; manifest 1.2.0 + `downloads` permission.
- earlier v2 — yt-dlp engine (`yt_dl.py`): YouTube/Vimeo/etc. delegate to yt-dlp; auto-detect by host + a New Download "Use yt-dlp" toggle; resolves real title/file. Added to requirements + spec.
- earlier v2 — v2 Queue Manager (🗂 Queues in sidebar): add/delete queues + per-queue concurrency.
- earlier v2 — Debug logging: Settings → Advanced toggle writes `hyperfetch.log` (task lifecycle, errors, server downloads).
- earlier v2 — SHA-256 verification: on finish, fetch `<url>.sha256` sidecar, compare, mismatch → Error (Settings → Advanced toggle).
- `7063014` — wired Network/Advanced settings to the engine: proxy, listen-port, disk-cache, pre-allocate, max-connections, "when complete".
- `8ef8282` — v2 settings Network section + sidebar polish (single collapse btn, smooth slide, compact gauge) + close-button prompt.
- `518b759` — **v2 GUI rewrite** (`gui2/`, `python main.py --v2`): widget cards, grouped list, sidebar, tabbed New Download, details drawer, 6-section settings, tray, scheduler, multi-select, shortcuts, toasts.
- `c1372ad` — torrent live progress + real save_path (Open File works) + in-app settings + nav counts + context menu.
- `f5f1f76` — v1.2.1 release (GUI redesign + torrent peer discovery).
- `0f94ba0` — BitTorrent / magnet via aria2c sidecar.
- `59a5a3b` — HLS quality variant picker.
- earlier — ABDM-style UI overhaul, multi-queue + adaptive segments, crash reporter + update check, v1.2.0 release.

## Bugs
- Open bugs are listed in **BUGS.md**, not here.

## Not possible with the current engine (checked, don't re-propose)
- **Per-file priority** (High/Normal/Low). aria2 has only `--select-file`, i.e. include/exclude. Skip works; ranking does not. Needs a different engine.
- **Peer blocklists / IP filtering** (PeerGuardian). No aria2 support, and the app does not own the socket layer.
- **Force reannounce / clear peers / ban peer.** No such RPC methods exist — the full method list was checked.
- **Relocating the `.aria2` control file.** aria2 offers only `--auto-save-interval` and `--remove-control-file`; there is no path option, and the file is what makes a torrent resumable. That staging route is built: Settings → General "Keep unfinished downloads in" (`utils.INCOMPLETE_DIR`; a finished torrent is moved home).
- **Torrent + HTTP mirrors for the same file.** aria2 does Metalink, but nothing tells us a mirror exists.
- **qBittorrent Web API compatibility.** Presupposes libtorrent; that is an engine rewrite.

## Already shipped (do not re-add)
- Piece map — the drawer's Overview shows a Downloaded and an Availability strip (`gui2/pieces_bar.py`).
- Queue from a phone — the web client (Settings → Web Client): its own login, separate from the extension's pairing token, and on the LAN only with a password of 8+ characters. No mDNS.
- Updates — the app fetches its own update, checks its SHA-256 against GitHub's and asks before installing (`gui2/app_update.py`). Nothing is silent; the installer is not signed.
- Dark/Light/System themes + accent presets — `palette.py`, Settings → Appearance.
- Duplicate detection — infohash for magnets AND `.torrent` files, plus URL matching, with Show Existing / Add Anyway.
- File tree with checkboxes and skip — drawer Files tab; the selection persists across pause, restart and closing the drawer.
- Batch link grabber — right-click → "Show all downloadable links…", panel in a closed shadow root, per-kind filters, tick and queue as one batch.
- Offline queue — a click while the app is closed is held in `chrome.storage.local` (bounded to 50, newest kept) and replayed on the next successful send / worker wake / browser start. Cookies are deliberately not persisted: `storage.local` is unencrypted and an item can sit for days.
- Context-aware right-click — torrent/magnet links, all images on the page, all links in the selection.
- DASH (`.mpd`) — sniffed by the extension and routed to yt-dlp, which parses the manifest and merges the video/audio adaptation sets with the bundled ffmpeg. Previously an `.mpd` fell through every engine and byte-downloaded the XML index as if it were the video.

## Decided to keep light (not building)
- Updates — never silent: the user is asked every time (see Already shipped).
- Crash reporter — local JSON dumps only (no networked endpoint).
- Telemetry, even opt-in — the app's one clear promise is localhost-only, no accounts, no remote services. A phone-home undermines it for data that GitHub issues already provide.
- Cloud storage offload (Drive/OneDrive/S3) — drags in account management and OAuth for a fetch-and-park use case.

## Notes
- HTTP/2/3 declined — multiplexing over one connection kills the multi-socket parallelism this app relies on.
- `bin/aria2c.exe` is fetched by `build.ps1` and bundled by the spec.
- Treat every big change as an invite for adversarial review — "tests pass" is necessary, not sufficient.
