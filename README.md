# Smart Download Manager (IDM clone)

Multi-segment download accelerator with a desktop GUI, IDM-style download
dialogs, persistent state, a download queue, and a Chrome/Edge extension that
intercepts browser downloads — like Internet Download Manager.

## Features
- **Segmented downloads** — up to 16 parallel connections per file (when the
  server supports HTTP `Range`), falls back to a single stream otherwise.
- **IDM-style dialogs** — every new download shows a *Download File Info* dialog
  (probed size/type, editable filename, save-to folder, priority,
  Download Now / Download Later). Double-click any row for full *Properties*
  (URL, path, segments, range support, error) with Open File / Open Folder /
  Copy URL.
- **Pause / Resume / Cancel** — resume continues from bytes already on disk,
  even after closing and reopening the app.
- **Persistent state** — the download list (including segment progress) survives
  restarts; in-flight downloads restore as paused and resumable. Stored in
  `%APPDATA%\SmartDownloadManager\`.
- **Rate-limit aware** — retries with exponential backoff (honors `Retry-After`),
  adaptively halves parallel connections on HTTP 429, staggers connection starts.
- **Download queue** — priority ordering + bounded concurrency + scheduler.
- **Settings** — default folder, concurrent downloads, connections per download.
- **Browser integration** — the extension catches downloads in Chrome/Edge and
  sends them to the app over a local Flask server; the file-info dialog pops up
  exactly like IDM. Toggle capture on/off from the extension popup.

## Architecture
| File | Role |
|------|------|
| `task.py` | `DownloadTask` + `Segment` state model, control flags, (de)serialization |
| `downloader.py` | segmented HTTP downloader (probe, ranges, retry/backoff, 429 throttle, merge) |
| `queue_manager.py` | priority queue, concurrency, scheduler (Condition-based) |
| `utils.py` | app-data dir, JSON persistence, filename derivation, unique paths |
| `api_server.py` | Flask `POST /download` + `GET /ping` for the extension |
| `main.py` | PySide6 GUI: table, dialogs, settings, embedded Flask server |
| `chrome_ext/` | MV3 browser extension (background interceptor, popup, content script) |

GUI and server share **one** queue, so browser downloads appear in the window
alongside manually added ones.

## Run
```powershell
pip install -r requirements.txt
python main.py          # or double-click IDM.bat
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
4. **Pair it (one time):** in the app open **⚙ Settings → Security**, copy the
   *pairing token*, then click the extension's toolbar icon and paste it into the
   **Pairing** box → Save.
5. Keep `main.py` running. Downloads of common file types are intercepted and the
   app shows the file-info dialog. If the app is offline the browser keeps the
   download (the extension pauses, asks the app, then cancels or resumes).
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
  `%APPDATA%\SmartDownloadManager\pair_token` (regenerate by deleting the file).
- **TLS verification ON by default** — toggle off only for trusted self-signed
  hosts in Settings (clearly warned).
- **Cookies/auth headers are never written to disk** — they stay in memory for
  the session and are stripped from `downloads.json`.
- **Path-traversal safe** — download filenames are confined to the chosen folder.

## Tests
```powershell
pip install -r requirements-dev.txt
pytest                 # 101 tests, fully offline (local HTTP servers)
pytest -m "not network"  # same — no test needs the internet
```
CI (`.github/workflows/ci.yml`) runs the suite on Windows + Linux, Python 3.10
and 3.12, a Node/jsdom test of the extension, and a Windows app build.

## Building a release (Windows)
```powershell
pip install -r requirements-dev.txt
.\build.ps1                 # -> dist\SmartDownloadManager\ (portable onedir app)
.\build.ps1 -Installer      # also builds dist\installer\SmartDownloadManager-1.0.0-setup.exe (needs Inno Setup 6)
```
The build runs a `--selftest` smoke check on the frozen binary. To **code-sign**
(recommended before public distribution — avoids SmartScreen warnings):
```powershell
.\build.ps1 -Installer -Sign -CertPath mycert.pfx -CertPass ****
```
Signing needs your own Authenticode certificate and the Windows SDK `signtool`.
The unsigned build runs fine for personal use; Windows SmartScreen will warn on
first launch until the binary is signed and has reputation.

`SmartDownloadManager.spec` drives PyInstaller (onedir, windowed, bundles the
icon + cryptography + the lazily-imported `hls` module). CI uploads the built
app as a downloadable artifact on every push.

## Notes
- Settings and download state live in `%APPDATA%\SmartDownloadManager\`.
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
