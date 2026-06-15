# Privacy Policy — HyperFetch

**Last updated: 2026-06-13**

HyperFetch is a desktop download accelerator plus a companion
browser extension. This policy explains exactly what the software does with
your data. The short version: **everything stays on your computer. There are no
remote servers, no accounts, no analytics, and no tracking.**

## Who this covers
- The **desktop app** (`HyperFetch.exe` / `main.py`).
- The **browser extension** ("HyperFetch").

## What data is processed

| Data | Why | Where it goes |
|------|-----|---------------|
| URLs of files/videos you choose to download | To download them | Sent only to the local app at `http://127.0.0.1:5000` and to the file's own server |
| Cookies for the download URL's site | So logged-in / paid downloads work (e.g. Google Drive) | Attached to that one download request only; **never stored, never sent anywhere else** |
| Page title / filename | To name the saved file | Stays local |
| The pairing token | To authorize the extension ↔ app link | Stored locally only |
| App settings + download list | To resume downloads and remember preferences | Stored locally in `%APPDATA%\HyperFetch\` |

## What the software does **not** do
- **No remote servers.** The app's only network listener is bound to
  `127.0.0.1` (your machine); it is unreachable from the internet.
- **No telemetry or analytics.** No usage data, crash reports, or identifiers
  are collected or transmitted.
- **No accounts, no sign-in, no cloud.**
- **No selling or sharing of data** with any third party.
- **Cookies are never written to disk.** They are forwarded with a single
  download request and held only in memory; they are explicitly stripped before
  the download list is saved (`utils.strip_sensitive`).

## Network connections the software makes
1. **Localhost only** — the extension talks to `http://127.0.0.1:5000` (the app).
2. **The download target** — the app connects to the exact URL of the file you
   chose to download (and, for HLS video, the segment/key URLs that playlist
   references). Nothing else.

## Permissions the extension requests, and why
See `STORE_LISTING.md` for the full per-permission justification. In summary:
`downloads`, `storage`, `webRequest`, and `cookies` exist solely to detect a
download, name it, and hand it (with the right session cookies) to your local
app. `<all_urls>` host access is required because a download manager must work
on any site you visit; it is **not** used to read or transmit page content
anywhere except for the download you explicitly start.

## Data retention and deletion
- Settings, the download list, and the pairing token live in
  `%APPDATA%\HyperFetch\`. Delete that folder to remove all app data.
- Extension settings live in the browser's local extension storage. Remove the
  extension to clear them.

## Children
The software is a general-purpose utility and is not directed at children.

## Changes
Material changes to this policy will update the "Last updated" date above.

## Contact
Questions about privacy: **tanumaygoswami2001@gmail.com**
