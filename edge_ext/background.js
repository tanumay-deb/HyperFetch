// Hand downloads to the local app (main.py / api_server.py) on explicit user
// action only — the right-click menu and the in-page video badges. Browser
// downloads are NOT auto-intercepted.
const APP = "http://127.0.0.1:5000/download";
const PROBE = "http://127.0.0.1:5000/probe";
const PAIR = "http://127.0.0.1:5000/pair";

const ignoreErr = () => void chrome.runtime.lastError;

// Auto-pairing: pull the token straight from the app instead of asking the user
// to copy/paste it. The app only answers /pair for THIS extension's id (locked
// via CORS there), so other extensions and websites can't read the token.
function fetchPairToken() {
  return fetch(PAIR)
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      const t = (j && j.token) || "";
      if (t) chrome.storage.local.set({ token: t });
      return t;
    })
    .catch(() => "");
}

// The stored token, or a freshly auto-paired one if none is stored yet.
function getToken() {
  return new Promise((resolve) => {
    chrome.storage.local.get({ token: "" }, ({ token }) =>
      token ? resolve(token) : fetchPairToken().then(resolve));
  });
}

// capture on/off toggle, cached for the high-frequency media sniffer. The
// pairing token is NOT cached in a global: MV3 evicts the service worker after
// ~30s idle, and a menu/message event wakes it before the async storage.get
// callback can hydrate a global — so a cached token would still be "" on that
// first wake-triggered send and the app would 401 it. Reading the token fresh
// from storage at send time (in sendToApp) avoids that race.
let captureEnabled = true;
chrome.storage.local.get({ enabled: true }, (v) => { captureEnabled = v.enabled; });
chrome.storage.onChanged.addListener((ch) => {
  if (ch.enabled) captureEnabled = ch.enabled.newValue;
});

// Send a download to the app WITH the browser's cookies for that URL —
// required for auth-gated hosts (Google Drive, attachments behind login).
// Authenticated with the pairing token (read fresh from storage so a freshly
// woken service worker never sends an empty one) so only this paired extension
// can queue.
function sendToApp(url, filename, referrer, done, extra) {
  getToken().then((token) => {
    chrome.cookies.getAll({ url }, (cookies) => {
      ignoreErr();
      const cookieStr = (cookies || []).map((c) => `${c.name}=${c.value}`).join("; ");
      const post = (tok) => fetch(APP, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-HyperFetch-Token": tok },
        body: JSON.stringify(Object.assign({
          url,
          filename: filename || "",
          cookies: cookieStr,
          referrer: referrer || "",
          userAgent: navigator.userAgent,
          token: tok
        }, extra || {}))
      });
      post(token)
        .then((r) => {
          // stale token (e.g. the app was reinstalled -> new token): drop it,
          // re-pair once, and retry so the user never has to re-pair by hand.
          if (r.status === 401) {
            chrome.storage.local.remove("token");
            return fetchPairToken().then((t2) => (t2 ? post(t2) : r));
          }
          return r;
        })
        .then((r) => r.json().then(
          (j) => done(r.ok, r.status, j),
          () => done(r.ok, r.status, {})))
        .catch(() => done(false, 0, {}));
    });
  });
}

// Manual capture only — nothing is auto-intercepted. A download reaches the app
// in exactly two ways, both explicit user actions: the right-click
// "Download with HyperFetch" menu item (below) and the in-page video badges
// (content.js). Browser-initiated downloads are left entirely to the browser,
// so re-requested or already-downloaded files never trigger a surprise dialog.
chrome.runtime.onInstalled.addListener((details) => {
  // first install (not updates): open the bundled welcome/onboarding page —
  // it live-checks for the desktop app and walks through the 3 steps
  if (details && details.reason === "install" && chrome.tabs && chrome.tabs.create) {
    chrome.tabs.create({ url: chrome.runtime.getURL("welcome.html") }, ignoreErr);
  }
  // removeAll() first so an extension UPDATE doesn't hit "duplicate id" — the
  // prior registration persists across update and create() with the same id
  // surfaces an error to chrome://extensions otherwise.
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "hyperfetch-download",
      title: "Download with HyperFetch",
      contexts: ["link", "image", "video", "audio"]
    }, ignoreErr);
    // Media-page sites (YouTube etc.) play via blob:/MSE, so there is no
    // capturable media URL — instead offer a PAGE-level item that sends the page
    // URL to the app, whose yt-dlp engine resolves the actual video. Shown only
    // on hosts the app routes through yt-dlp (list mirrors yt_dl._SITES).
    chrome.contextMenus.create({
      id: "hyperfetch-download-page",
      title: "Download video on this page with HyperFetch",
      contexts: ["page", "frame", "video"],
      documentUrlPatterns: [
        "*://*.youtube.com/*", "*://youtu.be/*", "*://*.vimeo.com/*",
        "*://*.dailymotion.com/*", "*://*.twitch.tv/*", "*://*.tiktok.com/*",
        "*://*.instagram.com/*", "*://*.facebook.com/*", "*://*.twitter.com/*",
        "*://*.x.com/*", "*://*.soundcloud.com/*", "*://*.reddit.com/*",
        "*://*.bilibili.com/*", "*://*.rumble.com/*", "*://*.ok.ru/*"
      ]
    }, ignoreErr);
  });
});

// after removal, Chrome opens this hosted feedback page (bundled pages are gone
// by then). Guarded: absent in the jsdom test harness.
if (chrome.runtime.setUninstallURL) {
  chrome.runtime.setUninstallURL("https://tanumay-deb.github.io/HyperFetch/uninstall.html", ignoreErr);
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== "hyperfetch-download" &&
      info.menuItemId !== "hyperfetch-download-page") return;
  let url, name;
  if (info.menuItemId === "hyperfetch-download-page") {
    // send the PAGE url — the app's yt-dlp engine resolves the real video
    url = (tab && tab.url) || info.pageUrl;
    name = "";                       // yt-dlp fills in the video title
  } else {
    url = info.linkUrl || info.srcUrl;
    name = url ? url.split("?")[0].split(/[\\/]/).pop() || "" : "";
  }
  if (!url || url.startsWith("blob:") || url.startsWith("data:")) return;
  const referrer = (tab && tab.url) || info.frameUrl || info.pageUrl || "";
  // read the capture flag fresh — the cached global may be unhydrated on a
  // wake-triggered click.
  chrome.storage.local.get({ enabled: true }, ({ enabled }) => {
    if (!enabled) return;
    sendToApp(url, name, referrer, (ok, status) => {
      // surface the result in the page (the menu has no UI of its own) so a
      // pairing/offline failure isn't silently swallowed.
      const text = ok ? "Sent to HyperFetch"
        : status === 401 ? "HyperFetch couldn't pair — open the app and try again"
        : "HyperFetch app offline";
      if (tab && tab.id >= 0)
        chrome.tabs.sendMessage(tab.id, { type: "HYPERFETCH_TOAST", text }, ignoreErr);
    });
  });
});

// Relay for content scripts (they cannot read cookies themselves).
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "DOWNLOAD_URL") {
    const ref = sender && sender.tab ? sender.tab.url : "";
    sendToApp(msg.url, msg.filename, ref,
      (ok, status) => sendResponse({ ok, unpaired: status === 401 }));
    return true; // keep the message channel open for the async response
  }
});

// Capture browser-initiated downloads (clicking a Download button/link) and
// route them to the app instead of Chrome. Respects the on/off toggle. The
// Chrome download is cancelled ONLY after the app accepts it, so when the app
// is offline/unpaired the browser download just proceeds normally — no capture
// loop and no lost file. Already-downloaded files don't re-fire onCreated, so
// this never resurrects the old "surprise dialog for old files" problem.
if (chrome.downloads && chrome.downloads.onCreated) {
  chrome.downloads.onCreated.addListener((item) => {
    if (!captureEnabled) return;
    const url = item.finalUrl || item.url || "";
    if (!/^https?:\/\//i.test(url)) return;      // skip blob:/data:/extension URLs
    const name = (item.filename || "").split(/[\\/]/).pop()
              || url.split("?")[0].split("/").pop() || "";
    // auto=true lets the app apply the Settings allowlist; it replies status
    // "ignored" for file types not on the list, so we leave the browser download
    // alone. Only cancel the browser's copy once the app has actually queued it.
    sendToApp(url, name, "", (ok, status, body) => {
      if (ok && body && body.status === "queued") chrome.downloads.cancel(item.id, () => {
        ignoreErr();
        chrome.downloads.erase({ id: item.id }, ignoreErr);
      });
    }, { auto: true });
  });
}

// --- Media Sniffer ---
// HLS/DASH playlists (the actual "grab the video" targets) and direct media.
const PLAYLIST_RE = /\.(m3u8|mpd)(\?.*)?$/i;
const MEDIA_FILE_RE = /\.(mp4|webm|m4v|mov|mkv|flv|m4a|mp3|aac|ogg|wav|flac)(\?.*)?$/i;
// individual streaming chunks — useless on their own, never surface them
const SEGMENT_RE = /\.(ts|m4s|aac|cmfv|cmfa)(\?.*)?$/i;
const SEGMENT_CT = /(mp2t|iso\.segment)/i;

// --- HLS master parsing -------------------------------------------------------
// A sniffed .m3u8 is usually a *master* listing several quality variants
// (1080p/720p/480p…), each a separate media playlist. The content script can't
// fetch+parse it (cross-origin CORS), but the worker can (host_permissions).
// We parse the master here and hand the variants to the panel so the user can
// pick a quality and see an estimated size — the master itself carries none.
// HLS master memo. Keyed by manifest URL; the value is the message payload that
// would have gone to the original tab. On a cache hit we *re-send* the cached
// payload to the new tabId rather than refusing — so opening the same video in
// a second tab still gets the variant panel without re-fetching the master.
// inFlightHls dedups concurrent webRequest hits during the awaiting fetch.
// Both clear on SW restart, which is fine: a master that failed to fetch or
// returned nothing useful (empty text, parse fail) is NOT cached, so the next
// webRequest hits will retry naturally.
const parsedHls = new Map();
const inFlightHls = new Set();

function resolveUrl(u, base) {
  try { return new URL(u, base).href; } catch (e) { return u; }
}

// Parse a master playlist into [{height, bandwidth, url}], best first.
// Returns [] if `text` is a media playlist (no #EXT-X-STREAM-INF), not a master.
function parseHlsVariants(text, baseUrl) {
  const lines = text.split(/\r?\n/);
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].startsWith("#EXT-X-STREAM-INF")) continue;
    const attrs = lines[i].slice(lines[i].indexOf(":") + 1);
    // anchor to a delimiter so AVERAGE-BANDWIDTH= (often listed first) can't
    // shadow the spec-required peak BANDWIDTH=.
    const bw = /(?:^|,)\s*BANDWIDTH=(\d+)/i.exec(attrs);
    const res = /(?:^|,)\s*RESOLUTION=\d+x(\d+)/i.exec(attrs);
    let uri = "";
    for (let j = i + 1; j < lines.length; j++) {
      const l = lines[j].trim();
      if (l && !l.startsWith("#")) { uri = l; break; }
    }
    if (!uri) continue;
    out.push({
      height: res ? parseInt(res[1], 10) : 0,
      bandwidth: bw ? parseInt(bw[1], 10) : 0,
      url: resolveUrl(uri, baseUrl)
    });
  }
  out.sort((a, b) => (b.height - a.height) || (b.bandwidth - a.bandwidth));
  return out;
}

// Sum #EXTINF segment durations (seconds) in a media playlist.
function hlsDuration(text) {
  let total = 0, m;
  const re = /#EXTINF:\s*([\d.]+)/gi;
  while ((m = re.exec(text))) total += parseFloat(m[1]) || 0;
  return total;
}

// Ask the desktop app to parse the master. The app has the original capture's
// cookies/referer/UA and no CORS, so it reads referer/auth-gated manifests the
// SW's own fetch can't. Returns the variant array, [] for single-quality, or
// null when the app is unreachable (-> caller falls back to the SW fetch).
function probeViaApp(url, referer) {
  return new Promise((resolve) => {
    // use the stored token only (no auto-pair fetch here) — the download path
    // pairs first, and an unpaired probe just falls back to the SW fetch below.
    chrome.storage.local.get({ token: "" }, ({ token }) => {
      chrome.cookies.getAll({ url }, (cookies) => {
        ignoreErr();
        const cookieStr = (cookies || []).map((c) => `${c.name}=${c.value}`).join("; ");
        fetch(PROBE, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-HyperFetch-Token": token },
          body: JSON.stringify({ url, cookies: cookieStr, referrer: referer || "",
                                 userAgent: navigator.userAgent, token })
        })
          .then((r) => (r.ok ? r.json() : null))
          .then((j) => resolve(j && Array.isArray(j.variants) ? j.variants : (j ? [] : null)))
          .catch(() => resolve(null));   // app offline -> fall back
      });
    });
  });
}

// Fallback: fetch + parse in the worker (works for same-origin / open CDNs).
async function probeViaFetch(url) {
  let text = "";
  try { text = await (await fetch(url, { credentials: "include" })).text(); }
  catch (e) { return null; }            // couldn't read it at all
  const variants = parseHlsVariants(text, url);
  if (!variants.length) return [];      // single-quality / unreadable-as-master
  let duration = 0;
  try { duration = hlsDuration(await (await fetch(variants[0].url, { credentials: "include" })).text()); }
  catch (e) { /* sizes omitted */ }
  return variants.map((v) => ({
    label: v.height ? v.height + "p"
      : (v.bandwidth ? Math.round(v.bandwidth / 1000) + " kbps" : "variant"),
    height: v.height, bandwidth: v.bandwidth, url: v.url,
    size: (duration && v.bandwidth) ? Math.round(v.bandwidth / 8 * duration) : 0
  }));
}

// Resolve a sniffed .m3u8 into a quality picker (master) or a single row.
async function handleHls(url, tabId, fallbackName, referer) {
  const cached = parsedHls.get(url);   // re-deliver to a new tab, no re-probe
  if (cached) {
    if (tabId >= 0) chrome.tabs.sendMessage(tabId, cached, ignoreErr);
    return;
  }
  if (inFlightHls.has(url)) return;
  inFlightHls.add(url);
  try {
    let variants = await probeViaApp(url, referer);   // app-side (auth path)
    const appAnswered = variants !== null;
    if (!appAnswered) variants = await probeViaFetch(url);  // SW fallback
    const definite = appAnswered || variants !== null;     // got a real answer?

    if (!variants || !variants.length) {
      const payload = { type: "SNIFFED_MEDIA", url, mime: "application/x-mpegurl",
                        size: 0, filename: fallbackName, kind: "hls" };
      if (tabId >= 0) chrome.tabs.sendMessage(tabId, payload, ignoreErr);
      // cache only on a definite answer + a real tab; a transient failure or a
      // tabId<0 background request must stay retryable for the next real tab.
      if (definite && tabId >= 0) parsedHls.set(url, payload);
      return;
    }

    const payload = { type: "SNIFFED_HLS_MASTER", url,
                      filename: fallbackName, variants };
    if (tabId >= 0) {
      chrome.tabs.sendMessage(tabId, payload, ignoreErr);
      parsedHls.set(url, payload);
    }
  } finally {
    inFlightHls.delete(url);
  }
}

chrome.webRequest.onResponseStarted.addListener(
  (details) => {
    if (!captureEnabled) return;

    let contentType = "";
    let contentLength = 0;
    if (details.responseHeaders) {
      for (const header of details.responseHeaders) {
        const name = header.name.toLowerCase();
        if (name === "content-type") contentType = header.value.toLowerCase();
        if (name === "content-length") contentLength = parseInt(header.value, 10) || 0;
      }
    }

    const urlNoQ = details.url.split("?")[0];

    // classify
    let kind = null;
    const isPlaylistCT = /mpegurl|dash\+xml/i.test(contentType);
    const isMediaCT = contentType.startsWith("video/") || contentType.startsWith("audio/");

    if (PLAYLIST_RE.test(details.url) || isPlaylistCT) {
      kind = "hls"; // .m3u8 / .mpd manifest
    } else if (SEGMENT_RE.test(details.url) || SEGMENT_CT.test(contentType)) {
      return; // streaming chunk — skip
    } else if (MEDIA_FILE_RE.test(details.url) || isMediaCT) {
      if (contentLength && contentLength < 100000) return; // skip tiny UI sounds
      kind = "file";
    } else {
      return;
    }

    let filename = urlNoQ.split("/").pop() || "media";
    if (!filename.includes(".") && isMediaCT) {
      const ext = contentType.split("/")[1].split(";")[0];
      if (ext) filename += "." + ext;
    }

    // HLS: resolve quality variants (app /probe, with SW fetch as fallback).
    // Pass the page URL so the app can send a real Referer to gated CDNs.
    if (kind === "hls") {
      const referer = details.documentUrl || details.originUrl || details.initiator || "";
      handleHls(details.url, details.tabId, filename, referer);
      return;
    }

    if (details.tabId >= 0) {
      chrome.tabs.sendMessage(details.tabId, {
        type: "SNIFFED_MEDIA",
        url: details.url,
        mime: contentType || kind,
        size: contentLength,
        filename: filename,
        kind: kind
      }, ignoreErr);
    }
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);
