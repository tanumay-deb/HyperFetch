# Privacy Policy — HyperFetch

**Last updated: 28 September 2026.** The same policy is published at
<https://tanumay-deb.github.io/HyperFetch/privacy.html>.

HyperFetch is a desktop download accelerator plus a companion browser
extension for Chrome, Edge and Firefox. **HyperFetch does not collect,
transmit, or store your personal data on any server we control.** The
extension hands what it handles to one place only: the HyperFetch application
running on your own computer, at `127.0.0.1`. There is no HyperFetch account,
no analytics, no telemetry, and no remote service that receives anything about
you or what you download.

## What the extension handles, and why

To hand a download to the desktop application, the extension passes it the
same information your browser would have used to fetch the file itself.

- **The download URL**, and the address of the page you were on, so the
  application can send a correct `Referer` to servers that require one.
- **Cookies for that specific address**, read with the browser's `cookies`
  permission and scoped to the URL being downloaded. This is what lets a file
  behind a login download successfully. Cookies for sites you are not
  downloading from are never read.
- **Your browser's user-agent string**, so the application identifies itself
  to the server the same way your browser did.
- **Page content, only to spot media.** A content script detects video
  streams and shows the download button. When you choose "Show all
  downloadable links", "Download all images" or "Download all links in
  selection", it reads the links on that page. It sends nothing anywhere until
  you click.
- **Media responses, to offer a quality.** The extension notes the address,
  type and size of video and audio responses on the page. To list a stream's
  qualities it may fetch that playlist from the same site, or ask the
  application to.

All of it is sent to `http://127.0.0.1:21456` (port `5000` for older versions
of the application) — a loopback address, meaning your own machine. Loopback
traffic never reaches your network, your router, or the internet.

**On Firefox**, the install prompt says the extension shares browsing
activity, website content and website activity. Mozilla asks every extension
to declare what it passes outside the browser, and this is that hand-off to the
application on your own computer. None of it reaches us.

## What the extension stores

In the browser's local extension storage, on your device:

- A **pairing token**, shared with your local application so that website
  JavaScript cannot send downloads to it. On Firefox, until the application
  has allowed it, the four-digit **pairing code** the application's question
  shows.
- The **local address** the application was found on.
- Your **toggle settings** from the popup, and the corner you moved the
  download button to.
- A short **queue of pending downloads** — address, file name and page — held
  only while the application is closed, for at most an hour, and cleared once
  delivered. Cookies are never part of it.

Uninstalling the extension removes all of it.

## The desktop application

The application does its work on your computer. It keeps its data in
`%APPDATA%\HyperFetch\`:

- Your **settings**, the **download list** — with cookies and sign-in headers
  removed before it is saved (`utils.strip_sensitive`) — and a **history** of
  finished downloads: name, address, size and where each was saved.
- The **pairing token**, the addresses of the **Firefox installs you allowed**
  to pair (Settings → Browser can forget them), and — only if you set up the
  web client — its **username and a scrypt hash of its password**.
- A **log** of warnings and errors (with Debug logging switched on, more
  detail, download addresses included), and **crash reports** — an error
  trace, version and platform, no download addresses — which stay on your
  computer unless you choose to share them.

It connects to:

- **The sites you download from** — the file's own server, a video site and
  its streams, and, with checksum verification switched on, a `.sha256` file
  beside the download.
- For **torrents and magnet links**, trackers — including a list of public
  trackers added to magnet links — and other peers. As with any torrent
  client, they see your IP address.
- **Cloudflare's DNS** (`cloudflare-dns.com`) — when your internet provider's
  DNS answers a site with a block page, the application looks that one site's
  name up there ("Get past provider blocks", on by default). With "DNS over
  HTTPS" switched on, every lookup goes there.
- **GitHub**, only when you press Check for Updates.
- **Your router**, to open the torrent listening port (UPnP, on by default,
  Settings → Torrents), and a **proxy**, if you set one up.

It listens on `127.0.0.1`, your own machine. Only if you allow home-network
access for the web client (Settings → Web Client) does it also listen on your
local network, behind the password you set. It sends no telemetry and no
usage data.

Delete `%APPDATA%\HyperFetch\` to remove everything the application stored.

## Why the extension's permissions are what they are

- `cookies` — to download files that require you to be signed in.
- `downloads` — to hand downloads the browser starts to the application, for
  the file types it lists (Settings → Browser → Auto Capture Links), and to
  cancel the browser's copy once the application has accepted it, so the file
  is not fetched twice. It is on unless you switch it off in the popup.
- `webRequest` — to notice video stream manifests (`.m3u8`) and media files
  that a browser cannot save on its own.
- `contextMenus` — the right-click "Download with HyperFetch" item.
- `storage` — the local settings described above.
- `<all_urls>` — because a download can start from any site. The permission is
  broad; what is done with it is not.

## What we never do

- We do not sell or transfer your data to third parties.
- We do not use your data for any purpose unrelated to the single function of
  the extension: sending a download to your own computer.
- We do not use your data to determine creditworthiness or for lending
  purposes.
- We do not run analytics, tracking, advertising, or fingerprinting of any
  kind.
- We do not operate a server that receives your browsing history, your
  downloads, or anything else about you.

## This website

The site is hosted on GitHub Pages, and its home page loads fonts from Google
Fonts. Both see your IP address, as with any website. We run no analytics on
it.

## Children

HyperFetch is a general-purpose download tool and is not directed at children
under 13. We do not knowingly collect information from anyone, of any age,
because we do not collect information at all.

## Changes

If this policy changes, the revised version will be published with an updated
date. Material changes to what the extension does with your data will also
appear in the extension's store listings.

## Contact

Questions about this policy, or about what HyperFetch does:
<https://github.com/tanumay-deb/HyperFetch/issues> or
**tanumaygoswami2001@gmail.com**.
