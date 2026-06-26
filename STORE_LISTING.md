# Chrome Web Store submission notes

Copy/paste material and reviewer justifications for publishing the
"HyperFetch" extension. The desktop app must be installed
separately (the extension is a thin bridge to it).

---

## Store listing copy

**Name:** HyperFetch

**Summary (132 chars max):**
Send browser downloads and streaming videos to the HyperFetch
desktop app for fast multi-connection downloading.

**Category:** Productivity

**Description:**
HyperFetch speeds up your downloads by handing them to the companion
desktop app, which downloads each file over multiple connections at once. It can
also grab HLS streaming videos (`.m3u8`) and save them as a single playable file.

Features:
- Multi-connection downloading — large files finish much faster
- One-click "Download" badge on videos, plus a media panel for streams
- Saves files using the real video/page title
- Works with login-gated downloads by forwarding your session for that one request
- Pause / resume / queue, all managed in the desktop app

Requires the free HyperFetch desktop app (link in the app/readme).
Everything runs locally on your computer — no accounts, no servers, no tracking.

---

## Single purpose (required field)

> The extension's single purpose is to detect downloadable files and videos in
> the browser and forward them to the user's locally installed Smart Download
> Manager desktop application for accelerated downloading.

---

## Permission justifications (paste into the dashboard)

**`contextMenus`**
> Used to add a single right-click menu item, "Download with HyperFetch", on
> links, images, audio, and video. This is the primary user-initiated way to send
> a download to the desktop app. The extension creates only this one menu entry
> and reads nothing from the page; it acts only when the user clicks it.

**`downloads`**
> Used to detect when the browser starts a download and to cancel the browser's
> copy once the desktop app accepts it, so the file isn't downloaded twice.

**`storage`**
> Stores the user's local preferences: the capture on/off toggle and the pairing
> token used to authenticate with the local desktop app. No browsing data.

**`webRequest`**
> Used to observe response Content-Type/URL of media responses so the extension
> can offer to download streaming videos (HLS `.m3u8`/DASH manifests). It is
> observational only — the extension does not block, redirect, or modify any
> request.

**`cookies`**
> Read only to attach the user's existing session cookies to a download the user
> explicitly initiates, so that login-gated or paid downloads (e.g. files behind
> a sign-in) succeed. Cookies are sent only to the user's local app for that one
> download and are never stored or transmitted elsewhere.

**Host permission `<all_urls>`**
> A download manager must work on whatever site the user is on, so the content
> script (download badge) and the media detector need to run on all sites. The
> extension does not read or transmit page content; it only acts on downloads
> the user starts. Connections are limited to `http://127.0.0.1:5000` (the local
> app) and the download's own URL.

**Host permission `http://127.0.0.1:5000/*`**
> The fixed local address of the companion desktop app the extension sends
> downloads to.

---

## Data use disclosures (Privacy practices tab)

- **Does the item collect or use data?** Yes — only as needed to function.
- **Personally identifiable / authentication info:** session cookies are read to
  authorize a user-initiated download. **Not** stored, **not** sold, **not**
  transferred to third parties; sent only to the user's own local application.
- **Web history / activity:** not collected.
- **Analytics / telemetry:** none.
- Certify: data is **not** sold, **not** used for unrelated purposes, **not**
  used for creditworthiness/lending.
- **Privacy policy URL:** host `PRIVACY.md` (e.g. on GitHub Pages) and paste the
  link here. Required because the extension handles authentication cookies.

---

## Remote code (Code-readiness tab)

Answer: **No, I am not using remote code.** All executable JS is bundled; the
extension only fetches *data* (localhost JSON + HLS `.m3u8` text it parses, never
executes). If the automated scan force-flags it, paste this on the Privacy
practices tab:

> HyperFetch does not load or execute remote code. All JavaScript ships inside the
> package: popup.html references only the bundled popup.js, and there are no
> external <script> tags. The code contains no eval(), new Function(),
> importScripts(), or dynamic import(). Its network requests fetch DATA only — JSON
> to the user's local companion app at http://127.0.0.1:5000 to hand off a
> download, and HLS (.m3u8) playlist TEXT from the current media site, which is
> parsed with string/regex operations and never executed. DOM HTML is assigned
> only from static, in-package string literals. Nothing executable is downloaded,
> evaluated, or run from any remote origin.

## Data use disclosures — checkbox answers
- **Authentication information:** YES — reads session cookies, forwards them to
  the local app for one user-initiated download.
- **Website content:** YES (conservative) — reads the page to find the media URL +
  title and transmits the download hyperlink/filename to the local app.
- Everything else (PII, health, financial, personal communications, location,
  web history, user activity): **NO.**
- Certify all three disclosures: not sold/transferred to third parties (the local
  app is the user's own software, the single-purpose use case), not used for
  unrelated purposes, not used for creditworthiness.

---

## Reviewer notes / known friction
- The `cookies` + `<all_urls>` + `webRequest` combination triggers extra review.
  All three are core to the single purpose (forward authenticated downloads from
  any site); none is used for data collection. The justifications above map each
  permission to that purpose.
- **Lower-friction alternative if review pushes back:** ship the extension
  unlisted / self-hosted (developer mode "Load unpacked"), or move `cookies` and
  host access behind `optional_permissions` requested at runtime per-site. This
  reduces the up-front warning surface at the cost of an extra click per new site.
- The desktop app is required for the extension to do anything; without it the
  extension's requests simply fail and the browser keeps the download.
