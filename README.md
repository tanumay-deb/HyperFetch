# HyperFetch

A fast, multi-connection download manager for Windows. It splits each file
across many connections, grabs streaming video and torrents, and pairs with a
browser extension that sends your downloads straight to the app.

![HyperFetch](assets/store/screenshot_1_main.png)

## Features

- **Multi-connection downloads** — up to 16 parallel connections per file for big speedups (falls back to a single stream when the server doesn't support it).
- **Pause, resume & queues** — resume continues from the bytes already on disk, even after a restart; organize downloads into named queues with their own limits.
- **Streaming video** — grab HLS (`.m3u8`) streams and save them as one playable file; yt-dlp handles YouTube, Vimeo, Twitch, etc. with a quality picker.
- **Torrents & magnets** — built in (aria2c), no separate client.
- **Browser integration** — right-click any link, click the badge on a video, or auto-capture browser downloads by file type. The Chrome/Edge extension feeds them to the app.
- **History & stats** — every completed download is logged with lifetime totals.
- **Smart networking** — global proxy, DNS-over-HTTPS, UPnP, speed limits, and optional SHA-256 verification.

## Install & run

```powershell
pip install -r requirements.txt
python main.py          # or double-click HyperFetch.bat
```

The window opens and a local server starts at `http://127.0.0.1:5000`.

Headless (queue downloads with no window):

```powershell
python api_server.py
```

## Browser extension

1. Open `chrome://extensions` (or `edge://extensions`) and enable **Developer mode**.
2. **Load unpacked** → select the `chrome_ext/` folder.
3. In the app, open **Settings → Browser Integration**, copy the pairing token, and paste it into the extension popup.
4. Keep the app running. Right-click a link → **Download with HyperFetch**, or turn on capture to route browser downloads automatically (pick which file types in Settings).

## How it works

| Part | Role |
|------|------|
| `downloader.py` | segmented HTTP downloader (ranges, retry/backoff, merge) |
| `torrent.py` / `hls.py` / `yt_dl.py` | torrent, HLS, and media-page engines |
| `queue_manager.py` | priority queue, concurrency, scheduler |
| `history.py` | completed-download log + stats |
| `api_server.py` | local Flask server the extension talks to |
| `gui2/` | the desktop GUI |
| `chrome_ext/` · `edge_ext/` | the browser extension (kept in sync) |

The GUI and the server share **one** queue, so browser-sent and manually-added
downloads land in the same list. Everything runs on your machine — no accounts,
no tracking. Settings and state live in `%APPDATA%\HyperFetch\`.

## Tests

```powershell
pip install -r requirements-dev.txt
pytest                                  # Python suite (fully offline)
cd chrome_ext/test && npm install && npm test   # extension tests
```

## Build (Windows)

```powershell
.\build.ps1             # -> dist\HyperFetch\ (portable app)
.\build.ps1 -Installer  # also builds the setup.exe (needs Inno Setup 6)
```

## License

MIT — see [LICENSE](LICENSE). Privacy policy: [PRIVACY.md](PRIVACY.md). Please
download only content you have the right to.
