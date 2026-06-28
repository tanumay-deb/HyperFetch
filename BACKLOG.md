# HyperFetch — Backlog

Simple running list. Newest first. Keep entries to one line.

## Done
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
- Search follow-up: add `date:` token + remember recent searches. [low]
- Dialog polish: unified `DialogHeader` + design tokens (radius/spacing/margins) in palette; consolidate inline QSS. [med — from code audit]
- Module splits (from code audit): `settings.py` page-builder mixin; shared `finalize_download()` for downloader+hls. [med]
- Card density polish: cards already show icon · file · % · progress · speed · ETA · status — tighten layout for faster at-a-glance scan. [low-med refinement]
- Animation polish: card add/remove + group transitions + drawer/toast easing. [low-med]

## Bugs
- _(none open)_ — verified in v2: responsive layout holds at min (940×560) and large (1500×900); errored row shows the message on the card + in the drawer Logs; Delete works on a selection; Complete popup has working buttons.

## Ideas
- Light theme (the Settings theme selector is dark-only today — Light/System are no-ops in gui2; needs a light palette + qss() theming).
- Per-host rules (cookies/segments per host).
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
