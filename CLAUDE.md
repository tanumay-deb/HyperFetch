# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

HyperFetch — an IDM-style multi-segment download accelerator. A PySide6
desktop GUI, a segmented HTTP downloader, an HLS (`.m3u8`) grabber, and a localhost
Flask server that a Chrome/Edge MV3 extension feeds browser downloads into. Pure
desktop, localhost-only; no accounts or remote services.

## Commands

```powershell
pip install -r requirements.txt
python main.py            # GUI; also starts Flask at http://127.0.0.1:5000 (or run IDM.bat)
python api_server.py      # headless: queues downloads immediately, no dialogs/GUI

pip install -r requirements-dev.txt
pytest                    # full suite, hermetic (in-process HTTP servers, temp app-data)
pytest tests/test_downloader.py::test_name   # single test
pytest -m "not network"   # skip the few internet-marked tests
python main.py --selftest # headless build smoke check (constructs app, ticks once, exits 0)

# Extension JS tests (jsdom)
cd chrome_ext/test; npm install; npm test

# Windows release build (PyInstaller onedir -> dist\HyperFetch\)
.\build.ps1                # add -Installer (needs Inno Setup 6), -Sign -CertPath x.pfx -CertPass ****
```

CI (`.github/workflows/ci.yml`) runs pytest on Windows+Linux × Python 3.10/3.12, the
node/jsdom extension tests, and a Windows PyInstaller build with `--selftest`.

## Architecture

The central design fact: **the GUI and the Flask server share one `QueueManager`
instance**, so browser-intercepted downloads and manually-added ones land in the same
list and scheduler. `main.py` owns the queue and passes it to `run_server(...)`.

Data/control flow for a download:
1. Extension (`chrome_ext/background.js`) intercepts a download, pauses Chrome's copy,
   POSTs URL + cookies/referer/UA to `/download` with the pairing token.
2. `api_server.py` validates token + URL scheme. In GUI mode it pushes onto a `pending`
   deque; `main.py`'s 500ms `QTimer` pops it and shows the IDM file-info dialog before
   queuing. In headless mode it queues immediately.
3. `QueueManager` (`queue_manager.py`) — `Condition`-based scheduler thread, priority
   heap, bounded `max_concurrent`. Runs each task in its own thread via `Downloader.run()`.
4. `Downloader` (`downloader.py`) probes size/range support, splits into N `Segment`s,
   spawns one thread per segment writing into a pre-allocated `{path}.hfdownload` temp file,
   then renames to the final path on success. HLS URLs are delegated to
   `hls.HlsDownloader` instead (segment fetch + AES-128 decrypt + concat to `.ts`).

Module map: `task.py` (state model), `downloader.py` (byte downloader), `hls.py`
(HLS), `queue_manager.py` (scheduler), `api_server.py` (Flask), `utils.py` (paths,
persistence, security primitives, rate limiter), `main.py` (~1400-line PySide6 GUI).

### Concurrency model (important when editing downloader/task/GUI)

`DownloadTask` fields are written **in place** by worker threads and read by the GUI's
500ms timer. Display reads rely on the GIL — there is no lock around progress fields.
Control flow uses `threading.Event`s: `request_pause()` / `request_cancel()` set events
that every streaming/sleeping/backoff loop polls so pause/cancel stays responsive. When
adding a long-running loop, poll `t.pause_requested` / `t.cancel_requested` in small
slices (the codebase uses ~0.2s) rather than blocking.

Rate limiting is a token bucket (`utils.RateLimiter`): a per-task limiter plus a global
`utils.global_limiter`, both `.wait(len(chunk))`-ed in the segment write loop.

### Persistence & state restoration

Settings and download state live in `%APPDATA%\HyperFetch\`
(`settings.json`, `downloads.json`, `pair_token`). Key rule in `task.from_dict`: any
task that was `DOWNLOADING`/`QUEUED` at shutdown is restored as `PAUSED` and resumable
from the bytes already in its `.hfdownload` file — never auto-restarted. Cookies/auth headers
are stripped (`utils.strip_sensitive`) before writing to disk and live only in memory.

### Security model (localhost-only)

Three layers gate `/download`, do not weaken them casually: (1) server bound to
`127.0.0.1`; (2) CORS allows only `chrome-extension://` / `moz-extension://` origins so
website JS fails preflight; (3) a per-install pairing token (`X-HyperFetch-Token`, constant-time
compared). `/ping` is intentionally open (connection status only). TLS verification is on
by default via `utils.VERIFY_TLS`; filenames are confined to the chosen folder
(`utils.safe_filename` / `unique_path`).

## Gotchas

- **`pytest.ini` sets `testpaths = tests`** — only `tests/` runs by default. The
  root-level `test_core.py`, `test_comprehensive.py`, `test_e2e.py` are legacy and are
  NOT part of the suite; add new tests under `tests/`.
- **Lazy imports must be declared to PyInstaller.** `hls` and `cryptography` are imported
  inside functions (not at module top) and are listed in `HyperFetch.spec`'s
  `hiddenimports` / `collect_all`. If you add a lazily-imported module, update the spec or
  the frozen build breaks.
- **`edge_ext/` mirrors `chrome_ext/` but has diverged** — when changing extension
  behavior, check whether the same change belongs in both. `chrome_ext/` is the one CI
  syntax-checks and tests.
- **`.hfdownload` = in-progress temp file, `.part*` = legacy**; both are gitignored and cleaned
  on completion/cancel.
- The GUI is a single large `DownloadApp` `QWidget` in `main.py` with inline dark-theme
  QSS at the top of the file; dialogs (`FileInfoDialog`, `PropertiesDialog`,
  `SettingsDialog`, completion popups) are separate classes above it.
