// Intercept browser downloads and hand them to the local app (main.py / api_server.py).
const APP = "http://127.0.0.1:5000/download";
const FILE_RE = /\.(zip|rar|7z|exe|msi|dmg|pkg|iso|img|bin|dat|deb|rpm|appimage|pdf|mp4|mkv|webm|avi|mov|mp3|m4a|flac|wav|ogg|docx|xlsx|pptx|epub|apk|tar|gz|bz2|xz|zst)(\?.*)?$/i;

const ignoreErr = () => void chrome.runtime.lastError;

// capture on/off toggle + pairing token (popup writes them; cached here and
// kept fresh via storage.onChanged so service-worker restarts pick them up).
let captureEnabled = true;
let pairToken = "";
chrome.storage.local.get({ enabled: true, token: "" }, (v) => {
  captureEnabled = v.enabled;
  pairToken = v.token || "";
});
chrome.storage.onChanged.addListener((ch) => {
  if (ch.enabled) captureEnabled = ch.enabled.newValue;
  if (ch.token) pairToken = ch.token.newValue || "";
});

// Send a download to the app WITH the browser's cookies for that URL —
// required for auth-gated hosts (Google Drive, attachments behind login).
// Authenticated with the pairing token so only this paired extension can queue.
function sendToApp(url, filename, referrer, done) {
  chrome.cookies.getAll({ url }, (cookies) => {
    ignoreErr();
    const cookieStr = (cookies || []).map((c) => `${c.name}=${c.value}`).join("; ");
    fetch(APP, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SDM-Token": pairToken },
      body: JSON.stringify({
        url,
        filename: filename || "",
        cookies: cookieStr,
        referrer: referrer || "",
        userAgent: navigator.userAgent,
        token: pairToken
      })
    })
      .then((r) => done(r.ok, r.status))
      .catch(() => done(false, 0));
  });
}

chrome.downloads.onCreated.addListener((item) => {
  if (!captureEnabled) return;
  const url = item.finalUrl || item.url;
  if (!url || url.startsWith("blob:") || url.startsWith("data:")) return;

  const name = item.filename ? item.filename.split(/[\\/]/).pop() : "";
  if (!FILE_RE.test(url) && !FILE_RE.test(name)) return;

  // Pause immediately so Chrome doesn't download in parallel while we ask the app.
  chrome.downloads.pause(item.id, ignoreErr);

  sendToApp(url, name, item.referrer, (ok) => {
    if (ok) {
      // App accepted: kill Chrome's copy and remove it from the download shelf.
      chrome.downloads.cancel(item.id, () => {
        ignoreErr();
        chrome.downloads.erase({ id: item.id }, ignoreErr);
      });
    } else {
      // App offline/refused -> let Chrome continue the download.
      chrome.downloads.resume(item.id, ignoreErr);
    }
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
