# HyperFetch (IDM clone)

Multi-segment download accelerator with a desktop GUI, IDM-style download
dialogs, persistent state, a download queue, and a Chrome/Edge extension that
hands downloads to the app on demand — right-click **Download with HyperFetch**,
or grab streaming video from the in-page badge.

## Features
- **Segmented downloads** — up to 16 parallel connections per file (when the
  server supports HTTP `Range`), falls back to a single stream otherwise.
- **BitTorrent & Magnet Links** — built-in support for downloading torrents and magnet links directly, without needing a separate client.
- **HLS (.m3u8) Grabber** — native support for fetching, decrypting (AES-128), and concatenating HTTP Live Streaming videos.
- **yt-dlp engine** — media pages (YouTube, Vimeo, Twitch, TikTok, etc.) are downloaded via yt-dlp; auto-detected by host or forced with the **Use yt-dlp** toggle in New Download, with a **quality picker** (Best / 1080p / 720p / 480p / Audio only) that uses single-file formats so no ffmpeg merge is required.
- **IDM-style dialogs** — every new download shows a *Download File Info* dialog
  (probed size/type, editable filename, save-to folder, priority,
  Download Now / Download Later). Double-click any row for full *Properties*
  (URL, path, segments, range support, error) with Open File / Open Folder /
  Copy URL.
- **Pause / Resume / Cancel** — resume continues from bytes already on disk,
  even after closing and reopening the app.
- **Persistent state** — the download list (including segment progress) survives
  restarts; in-flight downloads restore as paused and resumable. Stored in
  `%APPDATA%\HyperFetch\`.
- **Advanced Queueing & Scheduling** — priority ordering, bounded concurrency limits, a global **Time-based Scheduler** (start/stop overnight), and **Force Download** to bypass all limits.
- **Global Speed Limit** — throttle the application's maximum download speed so it doesn't saturate your entire network while you game or stream.
- **Multi-queue manager** — create named queues with their own concurrency, move tasks between them; the sidebar **Queues** dialog manages them.
- **Auto-categorization** — downloads sort into Videos / Music / Images / Compressed / Programs / Documents / Other by file type (sidebar filters + optional per-category subfolders).
- **Download history & stats** — completed downloads are logged to `history.json`; the sidebar **History** dialog shows lifetime totals (bytes downloaded, files completed, top category) and a searchable list with Open Folder / Copy URL / Clear.
- **SHA-256 verification** — optionally fetch a `<url>.sha256` sidecar after a download and flag a mismatch as failed (Settings → Advanced).
- **Network controls** — global proxy, DNS-over-HTTPS, torrent listen port, UPnP/NAT-PMP port mapping, disk cache, pre-allocation (Settings → Network/Advanced).
- **Debug logging** — optional `hyperfetch.log` for troubleshooting (Settings → Advanced).
- **Rate-limit aware** — retries with exponential backoff (honors `Retry-After`),
  adaptively halves parallel connections on HTTP 429, staggers connection starts.
- **Browser integration** — right-click any link/image/media → **Download with
  HyperFetch**, or click the in-page badge on a streaming video, to send it to
  the app over a local Flask server. With capture on, the extension also routes
  **browser-initiated downloads** (clicking a Download button) and **magnet /
  `.torrent` link clicks** to the app instead of the browser/OS handler — and
  hands a download to the app only after it accepts, so with the app closed the
  browser download just proceeds. Toggle capture on/off from the extension popup.

## Architecture
| File | Role |
|------|------|
| `task.py` | `DownloadTask` + `Segment` state model, control flags, (de)serialization |
| `downloader.py` | segmented HTTP downloader (probe, ranges, retry/backoff, 429 throttle, merge); delegates magnet/torrent/HLS/media-page engines |
| `torrent.py` | BitTorrent/magnet via an aria2c sidecar (`bin/aria2c.exe`) |
| `hls.py` | HLS (.m3u8) fetch + AES-128 decrypt + concat |
| `yt_dl.py` | yt-dlp engine for media pages (YouTube etc.) |
| `doh.py` / `upnp.py` | DNS-over-HTTPS resolver / UPnP IGD port mapping |
| `queue_manager.py` | priority queue, concurrency, scheduler (Condition-based) |
| `history.py` | completed-download log + stats (persisted to `history.json`) |
| `utils.py` | app-data dir, JSON persistence, filenames, TLS/proxy/network globals, logging |
| `api_server.py` | Flask `POST /download` + `/probe` + `GET /ping` for the extension |
| `main.py` | entry point; v2 GUI by default, `--v1` for the legacy GUI, `--selftest`, headless flags |
| `gui/` | legacy GUI (`--v1`) — `main_window`, `models`, `delegates`, `dialogs`, `theme`, `icons` |
| `gui2/` | v2 GUI (default) — widget cards, sidebar, drawer, tabbed dialogs, settings, toasts |
| `chrome_ext/` / `edge_ext/` | MV3 browser extension (kept in sync) |

GUI and server share **one** queue, so browser-sent downloads appear in the
window alongside manually added ones.

## Run
```powershell
pip install -r requirements.txt
python main.py          # v2 GUI (default), or double-click IDM.bat
python main.py --v1     # legacy table-based GUI
```
The window opens and a local server starts at `http://127.0.0.1:5000`.

Headless (no GUI, queues immediately without dialogs):
```powershell
python api_server.py
```

## Browser extension
1. Open `chrome://extensions` (or `edge://extensions`).
2. Enable **Developer mode**.
3. **Load unpacked** → select the `chrome_ext/` folder.
4. **Pair it (one time):** in the app open **⚙ Settings → Browser Integration**, copy
   the *pairing token*, then click the extension's toolbar icon and paste it into the
   **Pairing** box → Save.
5. Keep `main.py` running. **Right-click** a link/image/media → **Download with
   HyperFetch** (or click the badge on a streaming video). With capture **on**, the
   extension also routes browser-initiated downloads (Download buttons) and magnet/
   `.torrent` link clicks to the app; with the app closed the browser download just
   proceeds normally.
6. The popup shows connection + pairing status, a capture on/off toggle, and a
   test download button.

After editing extension files, press ↻ on the extension card.

## Security model
This is a localhost-only desktop app; it never listens off-machine. Defenses:
- **Server bound to `127.0.0.1`** — unreachable from the network.
- **CORS locked to extension origins** — a website's JavaScript cannot drive
  downloads (its cross-origin JSON POST fails preflight).
- **Pairing token** — `/download` requires the per-install token, so other local
  processes/extensions can't queue downloads. Token lives in
  `%APPDATA%\HyperFetch\pair_token` (regenerate by deleting the file).
- **TLS verification ON by default** — toggle off only for trusted self-signed
  hosts in Settings (clearly warned).
- **Cookies/auth headers are never written to disk** — they stay in memory for
  the session and are stripped from `downloads.json`.
- **Path-traversal safe** — download filenames are confined to the chosen folder.

## Tests
```powershell
pip install -r requirements-dev.txt
pytest                 # 164 tests, fully offline (local HTTP servers)
pytest -m "not network"  # same — no test needs the internet
```
CI (`.github/workflows/ci.yml`) runs the suite on Windows + Linux, Python 3.10
and 3.12, a Node/jsdom test of the extension, and a Windows app build.

## Building a release (Windows)
```powershell
pip install -r requirements-dev.txt
.\build.ps1                 # -> dist\HyperFetch\ (portable onedir app)
.\build.ps1 -Installer      # also builds dist\installer\HyperFetch-1.0.0-setup.exe (needs Inno Setup 6)
```
The build runs a `--selftest` smoke check on the frozen binary. To **code-sign**
(recommended before public distribution — avoids SmartScreen warnings):
```powershell
.\build.ps1 -Installer -Sign -CertPath mycert.pfx -CertPass ****
```
Signing needs your own Authenticode certificate and the Windows SDK `signtool`.
The unsigned build runs fine for personal use; Windows SmartScreen will warn on
first launch until the binary is signed and has reputation.

`HyperFetch.spec` drives PyInstaller (onedir, windowed, bundles the icon,
cryptography, yt-dlp, the lazily-imported `hls`/`doh`/`upnp` modules, and
`bin/aria2c.exe` when present). CI uploads the built app as a downloadable
artifact on every push.

## Notes
- Settings, download state, and completed-download history live in
  `%APPDATA%\HyperFetch\` (`settings.json`, `downloads.json`, `history.json`).
- Closing the app with active downloads asks for confirmation, pauses them,
  and resumes from disk on the next start.

## License & legal
- **License:** MIT — see [LICENSE](LICENSE).
- **Privacy:** everything stays on your machine; no servers, accounts, or
  tracking. See [PRIVACY.md](PRIVACY.md).
- **Acceptable use:** download only content you have the right to; no DRM
  circumvention. See [DISCLAIMER.md](DISCLAIMER.md).
- **Publishing the extension:** store copy + per-permission justifications are in
  [STORE_LISTING.md](STORE_LISTING.md). Host `PRIVACY.md` somewhere public and
  paste its URL into the Web Store privacy-policy field (required because the
  extension reads cookies).
