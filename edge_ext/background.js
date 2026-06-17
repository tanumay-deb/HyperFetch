// Hand downloads to the local app (main.py / api_server.py) on explicit user
// action only — the right-click menu and the in-page video badges. Browser
// downloads are NOT auto-intercepted.
const APP = "http://127.0.0.1:5000/download";

const ignoreErr = () => void chrome.runtime.lastError;

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
function sendToApp(url, filename, referrer, done) {
  chrome.storage.local.get({ token: "" }, ({ token }) => {
    chrome.cookies.getAll({ url }, (cookies) => {
      ignoreErr();
      const cookieStr = (cookies || []).map((c) => `${c.name}=${c.value}`).join("; ");
      fetch(APP, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-HyperFetch-Token": token },
        body: JSON.stringify({
          url,
          filename: filename || "",
          cookies: cookieStr,
          referrer: referrer || "",
          userAgent: navigator.userAgent,
          token
        })
      })
        .then((r) => done(r.ok, r.status))
        .catch(() => done(false, 0));
    });
  });
}

// Manual capture only — nothing is auto-intercepted. A download reaches the app
// in exactly two ways, both explicit user actions: the right-click
// "Download with HyperFetch" menu item (below) and the in-page video badges
// (content.js). Browser-initiated downloads are left entirely to the browser,
// so re-requested or already-downloaded files never trigger a surprise dialog.
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "hyperfetch-download",
    title: "Download with HyperFetch",
    contexts: ["link", "image", "video", "audio"]
  }, ignoreErr);
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== "hyperfetch-download") return;
  const url = info.linkUrl || info.srcUrl;
  if (!url || url.startsWith("blob:") || url.startsWith("data:")) return;
  const referrer = (tab && tab.url) || info.frameUrl || info.pageUrl || "";
  const name = url.split("?")[0].split(/[\\/]/).pop() || "";
  // read the capture flag fresh — the cached global may be unhydrated on a
  // wake-triggered click.
  chrome.storage.local.get({ enabled: true }, ({ enabled }) => {
    if (!enabled) return;
    sendToApp(url, name, referrer, (ok, status) => {
      // surface the result in the page (the menu has no UI of its own) so a
      // pairing/offline failure isn't silently swallowed.
      const text = ok ? "Sent to HyperFetch"
        : status === 401 ? "Pair the extension first — paste the app token in its popup"
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
// `parsedHls` marks masters we've SUCCESSFULLY processed (message posted).
// `inFlightHls` dedups concurrent webRequest hits while the fetch is awaiting.
// Both clear on SW restart, which is exactly what we want — a master that
// failed to post (SW evicted mid-fetch, fetch threw, parse returned empty)
// becomes eligible for retry the next time the player asks for it.
const parsedHls = new Set();
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

// Fetch a sniffed .m3u8; if it's a master, push its quality variants (with
// estimated sizes) to the panel. Non-masters fall through to a single entry.
async function handleHls(url, tabId, fallbackName) {
  if (parsedHls.has(url) || inFlightHls.has(url)) return;
  inFlightHls.add(url);
  try {
    let text = "";
    // credentials:include -> send the site's cookies (auth-gated manifests); the
    // worker's host_permissions make the cross-origin response readable regardless.
    try { text = await (await fetch(url, { credentials: "include" })).text(); }
    catch (e) { /* offline/blocked */ }
    const variants = parseHlsVariants(text, url);

    if (!variants.length) {
      // a single-quality media playlist (or DASH/unreadable) — one plain row
      if (tabId >= 0) chrome.tabs.sendMessage(tabId, {
        type: "SNIFFED_MEDIA", url, mime: "application/x-mpegurl",
        size: 0, filename: fallbackName, kind: "hls"
      }, ignoreErr);
      parsedHls.add(url);
      return;
    }

    // duration is identical across variants — fetch the top one once and reuse it
    let duration = 0;
    try { duration = hlsDuration(await (await fetch(variants[0].url, { credentials: "include" })).text()); }
    catch (e) { /* leave 0 -> sizes omitted */ }

    const enriched = variants.map((v) => ({
      label: v.height ? v.height + "p"
        : (v.bandwidth ? Math.round(v.bandwidth / 1000) + " kbps" : "variant"),
      height: v.height,
      bandwidth: v.bandwidth,
      url: v.url,
      size: (duration && v.bandwidth) ? Math.round(v.bandwidth / 8 * duration) : 0
    }));

    if (tabId >= 0) chrome.tabs.sendMessage(tabId, {
      type: "SNIFFED_HLS_MASTER", url, filename: fallbackName, variants: enriched
    }, ignoreErr);
    parsedHls.add(url);   // only mark "done" after the message is actually posted
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

    // HLS: fetch+parse in the worker so the panel can list quality variants.
    if (kind === "hls") {
      handleHls(details.url, details.tabId, filename);
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
