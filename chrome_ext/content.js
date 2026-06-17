// Manual capture only: a file reaches the app when the user clicks an in-page
// video badge (below) or the right-click "Download with HyperFetch" menu
// (background.js). Normal link clicks are left to the browser — nothing is
// auto-intercepted, so already-downloaded files never re-trigger a dialog.

let captureEnabled = true;
chrome.storage.local.get({ enabled: true }, (v) => { captureEnabled = v.enabled; });
chrome.storage.onChanged.addListener((ch) => {
  if (ch.enabled) {
    captureEnabled = ch.enabled.newValue;
    // reflect the toggle on the download badges right away
    if (typeof scheduleReposition === 'function') scheduleReposition();
  }
});

function sendToApp(url, suggestedName = null) {
  const filename = suggestedName || url.split("/").pop().split("?")[0];
  // route via the background worker so the browser's cookies for this URL
  // are attached (needed for Google Drive and other login-gated downloads)
  chrome.runtime.sendMessage({ type: "DOWNLOAD_URL", url, filename }, (res) => {
    if (res && res.unpaired) {
      showToast("Pair the extension first — open its popup and paste the app token");
      return;
    }
    if (chrome.runtime.lastError || !res || !res.ok) {
      // app offline -> fall back to a normal browser download
      showToast("App offline — downloading in browser");
      window.location.href = url;
    } else {
      showToast("Sent to Download Manager");
    }
  });
}

function showToast(msg) {
  const toast = document.createElement("div");
  toast.textContent = msg;
  toast.style.cssText = `
    position: fixed; bottom: 20px; right: 20px;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    color: white; padding: 12px 18px; border-radius: 10px;
    font: 600 13px 'Segoe UI', sans-serif; z-index: 2147483647;
    box-shadow: 0 4px 16px rgba(0,0,0,.45);
  `;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// --- Media Sniffer UI ---
const sniffedMedia = new Map();
// variant-playlist URLs that belong to a parsed master — suppressed as standalone
// rows so a master isn't duplicated by the chunklists the player also requests.
const hlsVariantUrls = new Set();
let panelRoot = null;
let panelContainer = null;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "SNIFFED_MEDIA") {
    addSniffedMedia(msg);
  } else if (msg.type === "SNIFFED_HLS_MASTER") {
    addHlsMaster(msg);
  } else if (msg.type === "HYPERFETCH_TOAST") {
    // result of a right-click "Download with HyperFetch" (the menu has no UI)
    showToast(msg.text);
  }
});

function formatSize(bytes) {
  if (!bytes) return "Unknown size";
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(1)} ${units[i]}`;
}

function updatePanel() {
  if (sniffedMedia.size === 0) return;
  
  if (!panelContainer) {
    panelContainer = document.createElement("div");
    panelContainer.id = "hyperfetch-media-sniffer-root";
    
    // Attach Shadow DOM to prevent host page CSS conflicts
    panelRoot = panelContainer.attachShadow({mode: 'closed'});
    
    const style = document.createElement("style");
    style.textContent = `
      #wrapper {
        position: fixed; top: 20px; right: 20px;
        z-index: 2147483647;
        font-family: 'Segoe UI', system-ui, sans-serif;
      }
      #toggle {
        background: linear-gradient(135deg, #6366f1, #8b5cf6);
        color: white; border: none; border-radius: 20px;
        padding: 10px 16px; font-weight: bold; cursor: pointer;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        display: flex; align-items: center; gap: 8px;
        transition: transform 0.2s; font-size: 13px;
      }
      #toggle:hover { transform: scale(1.05); }
      #list-container {
        display: none; background: #111a2e;
        border: 1px solid #243352; border-radius: 12px;
        margin-bottom: 10px; width: 320px; max-height: 400px;
        overflow-y: auto; box-shadow: 0 8px 24px rgba(0,0,0,0.5);
        color: #e5e9f5; flex-direction: column;
      }
      .header {
        padding: 12px 16px; background: #1a2540;
        border-bottom: 1px solid #243352; font-weight: 600;
        border-radius: 12px 12px 0 0; display: flex;
        justify-content: space-between; align-items: center; font-size: 14px;
      }
      .close-btn { 
        cursor: pointer; color: #8b97b8; background: none; 
        border: none; font-size: 18px; padding: 0; 
      }
      .close-btn:hover { color: white; }
      .item {
        padding: 12px 16px; border-bottom: 1px solid #243352;
        display: flex; flex-direction: column; gap: 6px;
      }
      .item:last-child { border-bottom: none; }
      .filename { font-weight: 600; font-size: 13px; word-break: break-all; }
      .meta { font-size: 11px; color: #8b97b8; }
      .variant-row {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; padding-top: 6px;
      }
      .variant-row .meta { flex: 1; }
      .btn-download {
        background: #243352; color: white; border: none;
        padding: 6px 12px; border-radius: 6px; cursor: pointer;
        font-size: 12px; font-weight: bold; align-self: flex-start;
      }
      .btn-download:hover { background: #6366f1; }
      ::-webkit-scrollbar { width: 8px; }
      ::-webkit-scrollbar-thumb { background: #243352; border-radius: 4px; }
      ::-webkit-scrollbar-thumb:hover { background: #6366f1; }
    `;
    panelRoot.appendChild(style);
    
    const wrapper = document.createElement("div");
    wrapper.id = "wrapper";
    
    const listContainer = document.createElement("div");
    listContainer.id = "list-container";
    
    const header = document.createElement("div");
    header.className = "header";
    header.innerHTML = `<span>Media Found</span><button class="close-btn">&times;</button>`;
    header.querySelector(".close-btn").onclick = () => {
      listContainer.style.display = "none";
    };
    listContainer.appendChild(header);
    
    const itemsDiv = document.createElement("div");
    itemsDiv.id = "items";
    listContainer.appendChild(itemsDiv);
    
    const toggle = document.createElement("button");
    toggle.id = "toggle";
    toggle.onclick = () => {
      listContainer.style.display = listContainer.style.display === "flex" ? "none" : "flex";
    };
    
    wrapper.appendChild(listContainer);
    wrapper.appendChild(toggle);
    panelRoot.appendChild(wrapper);
    document.body.appendChild(panelContainer);
  }
  
  const toggle = panelRoot.getElementById("toggle");
  toggle.innerHTML = `<span>⬇️ Download Media (${sniffedMedia.size})</span>`;
  
  const itemsDiv = panelRoot.getElementById("items");
  itemsDiv.innerHTML = "";
  
  Array.from(sniffedMedia.values()).reverse().forEach(media => {
    const item = document.createElement("div");
    item.className = "item";
    if (media.isMaster) renderMasterItem(item, media);
    else renderSingleItem(item, media);
    itemsDiv.appendChild(item);
  });
}

const STREAM_BADGE =
  '<span style="background:#6366f1;color:#fff;border-radius:4px;' +
  'padding:1px 6px;font-size:10px;margin-left:6px;">STREAM</span>';

function renderSingleItem(item, media) {
  // Built with DOM methods (not innerHTML) — media.filename / media.mime are
  // host-controlled. media.mime is the raw Content-Type header verbatim, so a
  // malicious origin could otherwise inject e.g. `<img src=x onerror=...>` and
  // get script execution inside our shadow DOM.
  const name = document.createElement("div");
  name.className = "filename";
  name.textContent = media.filename || "";
  if (media.kind === "hls") name.insertAdjacentHTML("beforeend", STREAM_BADGE);
  const meta = document.createElement("div");
  meta.className = "meta";
  const sizeTxt = media.kind === "hls" ? "Video stream" : formatSize(media.size);
  meta.textContent = `${sizeTxt} • ${media.mime || 'unknown'}`;
  const btn = document.createElement("button");
  btn.className = "btn-download";
  btn.textContent = "Download with HyperFetch";
  btn.onclick = (e) => {
    e.preventDefault();
    sendToApp(media.url, media.filename);
  };
  item.appendChild(name);
  item.appendChild(meta);
  item.appendChild(btn);
}

function bitrateText(bps) {
  if (!bps) return '';
  return bps >= 1e6 ? (bps / 1e6).toFixed(1) + ' Mbps'
                    : Math.round(bps / 1e3) + ' kbps';
}

// A master playlist: title header + one selectable row per quality variant.
// Built with DOM methods (not innerHTML) since variant URLs/labels are
// host-controlled.
function renderMasterItem(item, media) {
  const head = document.createElement("div");
  head.className = "filename";
  head.textContent = media.title;
  head.insertAdjacentHTML("beforeend", STREAM_BADGE);
  item.appendChild(head);

  media.variants.forEach((v) => {
    const row = document.createElement("div");
    row.className = "variant-row";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [v.label, v.size ? '~' + formatSize(v.size) : null,
                        bitrateText(v.bandwidth)].filter(Boolean).join(' • ');

    const btn = document.createElement("button");
    btn.className = "btn-download";
    btn.textContent = "Download " + v.label;
    btn.onclick = (e) => {
      e.preventDefault();
      sendToApp(v.url, media.title + ' ' + v.label + '.m3u8');
    };

    row.appendChild(meta);
    row.appendChild(btn);
    item.appendChild(row);
  });
}

function addSniffedMedia(media) {
  if (hlsVariantUrls.has(media.url)) return;  // already listed under a master
  if (sniffedMedia.has(media.url)) return;
  // generic stream names (master.m3u8, index.m3u8…) -> use the page title
  if (!media.filename || isGenericName(media.filename)) {
    media.filename = buildName(sanitizeName(document.title), media.url, media.kind);
  }
  sniffedMedia.set(media.url, media);
  updatePanel();
  // a sniffed stream can make a blob/MSE <video> downloadable -> attach a badge
  try { syncOverlays(); scheduleReposition(); } catch (e) { /* ignore */ }
}

// A parsed HLS master: one entry exposing every quality variant. `url` is the
// best variant so the existing badge / bestSniffedStream path grabs top quality.
function addHlsMaster(msg) {
  const variants = msg.variants || [];
  variants.forEach((v) => {
    hlsVariantUrls.add(v.url);
    sniffedMedia.delete(v.url);  // drop a chunklist row that arrived before the master
  });
  const existing = sniffedMedia.get(msg.url);
  if (existing && existing.isMaster) return;
  const title = sanitizeName(document.title) || 'video';
  const best = variants[0];
  sniffedMedia.set(msg.url, {
    url: best ? best.url : msg.url,
    kind: 'hls',
    isMaster: true,
    title: title,
    variants: variants,
    size: best ? best.size : 0,
    mime: 'application/x-mpegurl'
  });
  updatePanel();
  try { syncOverlays(); scheduleReposition(); } catch (e) { /* ignore */ }
}

function filenameFromUrl(url) {
  return (url || '').split('?')[0].split('/').pop() || 'media_file';
}

// ---- name the saved file after the actual video title, not the URL ----
function sanitizeName(s) {
  return (s || '')
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, ' ')  // illegal filename chars
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[. ]+$/, '')                    // no trailing dot/space
    .slice(0, 120);
}

function extFromUrl(url) {
  const m = (url || '').split('?')[0].match(/\.([a-z0-9]{2,5})$/i);
  return m ? m[1].toLowerCase() : '';
}

// Generic stream filenames carry no information — treat them as "no name".
function isGenericName(name) {
  return /^(master|index|playlist|chunklist|prog_index|media|video|stream|manifest)\.?/i
    .test((name || '').trim());
}

// Best human title for a video element: element hints -> nearby heading ->
// page og:title / <title>.
function videoTitle(video) {
  const attrs = [
    video.getAttribute && video.getAttribute('title'),
    video.getAttribute && video.getAttribute('aria-label'),
    video.getAttribute && video.getAttribute('data-title'),
  ];
  for (const a of attrs) {
    const c = sanitizeName(a);
    if (c) return c;
  }
  // climb a few ancestors looking for a caption/heading/title element
  let el = video;
  for (let depth = 0; el && depth < 5; depth++, el = el.parentElement) {
    const cap = el.querySelector &&
      el.querySelector('figcaption, h1, h2, [itemprop="name"], [class*="title" i]');
    if (cap) {
      const c = sanitizeName(cap.textContent);
      if (c && c.length > 1) return c;
    }
  }
  const meta = document.querySelector(
    'meta[property="og:title"], meta[name="og:title"], meta[name="twitter:title"]');
  const metaTitle = sanitizeName(meta && meta.getAttribute('content'));
  if (metaTitle) return metaTitle;
  return sanitizeName(document.title);
}

// Build the saved filename: <title>.<ext>. HLS stays .m3u8 so the app rewrites
// it to .ts; direct files keep their real extension.
function buildName(title, url, kind) {
  const ext = kind === 'hls' ? 'm3u8' : (extFromUrl(url) || 'mp4');
  let base = sanitizeName(title);
  if (!base) {
    const fromUrl = filenameFromUrl(url).replace(/\.[a-z0-9]+$/i, '');
    base = isGenericName(fromUrl) ? '' : sanitizeName(fromUrl);
  }
  if (!base) base = 'video';
  base = base.replace(new RegExp('\\.' + ext + '$', 'i'), '');  // avoid double ext
  return base + '.' + ext;
}

// ===================================================================
//  Per-element download badge
//  A floating "Download" button pinned to the TOP-RIGHT corner of every
//  downloadable <video>. It must NOT be a child of <video> (children of a
//  <video> are fallback content and never render). Instead each badge is a
//  position:fixed shadow-DOM host tracked to the video's bounding rect, so it
//  works for blob:/MSE players too (via a network-sniffed stream) and follows
//  scroll / resize / fullscreen.
// ===================================================================
const videoOverlays = new Map();   // HTMLVideoElement -> { host, btn }
const MIN_W = 120, MIN_H = 68;     // skip tiny thumbnails / tracking pixels

// Pick the best network-sniffed stream for a blob/MSE video (prefer HLS, newest).
function bestSniffedStream() {
  const arr = Array.from(sniffedMedia.values());
  let fallback = null;
  for (let i = arr.length - 1; i >= 0; i--) {
    const m = arr[i];
    if (!m.url || m.url.startsWith('blob:') || m.url.startsWith('data:')) continue;
    if (m.kind === 'hls') return m;
    if (!fallback) fallback = m;
  }
  return fallback;
}

// Resolve what a video element would download, or null if nothing grabbable.
// The saved filename is the video's title (from the page), not the URL.
function getVideoDownload(video) {
  const title = videoTitle(video);
  const cands = [video.currentSrc, video.src];
  video.querySelectorAll('source').forEach((s) => cands.push(s.src));
  for (const u of cands) {
    if (u && /^https?:/i.test(u)) {
      const kind = /\.m3u8(\?.*)?$/i.test(u) ? 'hls' : 'file';
      const m = sniffedMedia.get(u);
      const variants = m && m.variants ? m.variants : [];
      return { url: u, kind, filename: buildName(title, u, kind), variants };
    }
  }
  // MSE / blob element with no direct file -> fall back to a sniffed stream
  const src = video.currentSrc || video.src || '';
  if (!src || src.startsWith('blob:') || src.startsWith('mediasource:')) {
    const s = bestSniffedStream();
    if (s) return { url: s.url, kind: s.kind, filename: buildName(title, s.url, s.kind), variants: s.variants || [] };
  }
  return null;
}

function createOverlay(video) {
  const host = document.createElement('div');
  host.className = 'hyperfetch-video-badge';
  host.style.cssText =
    'position:fixed;z-index:2147483647;margin:0;padding:0;border:0;' +
    'background:transparent;pointer-events:none;display:none;';
  const shadow = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = `
    .badge-container {
      position: relative;
      display: inline-block;
      pointer-events: auto;
    }
    .btn-group {
      display: flex;
      align-items: stretch;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      border-radius: 999px;
      box-shadow: 0 4px 14px rgba(0,0,0,.45);
      opacity: 0.9;
      transition: opacity 0.15s, transform 0.15s;
    }
    .btn-group:hover {
      opacity: 1;
      transform: scale(1.05);
    }
    .btn {
      border: none; background: transparent; color: #fff;
      font: 700 12px/1 'Segoe UI', system-ui, sans-serif;
      padding: 7px 12px; cursor: pointer; white-space: nowrap;
      display: inline-flex; align-items: center; gap: 6px;
    }
    .btn-arrow {
      border: none; background: transparent; color: #fff;
      padding: 0 8px 0 4px; cursor: pointer;
      display: none; align-items: center; justify-content: center;
      border-left: 1px solid rgba(255,255,255,0.2);
    }
    .menu {
      position: absolute; top: 100%; right: 0; margin-top: 8px;
      background: #111a2e; border: 1px solid #243352;
      border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.5);
      display: none; flex-direction: column; min-width: 180px;
      overflow: hidden; font-family: 'Segoe UI', system-ui, sans-serif;
    }
    .menu.open { display: flex; }
    .menu-item {
      padding: 10px 14px; background: transparent; border: none;
      color: #e5e9f5; font-size: 12px; cursor: pointer;
      text-align: left; display: flex; flex-direction: column; gap: 4px;
      border-bottom: 1px solid #243352;
    }
    .menu-item:last-child { border-bottom: none; }
    .menu-item:hover { background: #1a2540; }
    .menu-item .meta { font-size: 10px; color: #8b97b8; }
    .ico { font-size: 13px; line-height: 1; }
  `;

  const container = document.createElement('div');
  container.className = 'badge-container';

  const group = document.createElement('div');
  group.className = 'btn-group';

  const btnMain = document.createElement('button');
  btnMain.className = 'btn';
  btnMain.type = 'button';
  btnMain.innerHTML = '<span class="ico">⬇</span><span class="lbl">Download</span>';

  const btnArrow = document.createElement('button');
  btnArrow.className = 'btn-arrow';
  btnArrow.type = 'button';
  btnArrow.innerHTML = '▼';

  const menu = document.createElement('div');
  menu.className = 'menu';

  group.appendChild(btnMain);
  group.appendChild(btnArrow);
  container.appendChild(group);
  container.appendChild(menu);
  shadow.appendChild(style);
  shadow.appendChild(container);

  let currentDl = null;

  btnMain.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!currentDl) return;
    sendToApp(currentDl.url, currentDl.filename);
    menu.classList.remove('open');
    const lbl = btnMain.querySelector('.lbl');
    if (lbl) { lbl.textContent = 'Sent ✓'; setTimeout(() => { lbl.textContent = 'Download'; }, 2000); }
  });

  btnArrow.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    menu.classList.toggle('open');
  });

  container.addEventListener('mouseleave', () => {
    menu.classList.remove('open');
  });

  (document.fullscreenElement || document.body).appendChild(host);

  const updateState = (dl) => {
    currentDl = dl;
    if (dl && dl.variants && dl.variants.length > 1) {
      btnArrow.style.display = 'flex';
      menu.innerHTML = '';
      dl.variants.forEach(v => {
        const item = document.createElement('button');
        item.className = 'menu-item';
        
        const label = document.createElement('span');
        label.style.fontWeight = 'bold';
        label.textContent = "Download " + v.label;
        item.appendChild(label);

        const meta = document.createElement('span');
        meta.className = 'meta';
        const metaText = [v.size ? '~' + formatSize(v.size) : null, bitrateText(v.bandwidth)].filter(Boolean).join(' • ');
        if (metaText) {
            meta.textContent = metaText;
            item.appendChild(meta);
        }

        item.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          const name = currentDl.filename.replace(/\.m3u8$/i, '') + ' ' + v.label + '.m3u8';
          sendToApp(v.url, name);
          menu.classList.remove('open');
          const lbl = btnMain.querySelector('.lbl');
          if (lbl) { lbl.textContent = 'Sent ✓'; setTimeout(() => { lbl.textContent = 'Download'; }, 2000); }
        };
        menu.appendChild(item);
      });
    } else {
      btnArrow.style.display = 'none';
      menu.innerHTML = '';
      menu.classList.remove('open');
    }
  };

  return { host, updateState };
}

function positionOverlay(video, ov) {
  const r = video.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const visible =
    captureEnabled && video.isConnected &&
    r.width >= MIN_W && r.height >= MIN_H &&
    r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
  if (!visible) { ov.host.style.display = 'none'; return; }
  ov.host.style.display = 'block';
  const hw = ov.host.offsetWidth || 118, hh = ov.host.offsetHeight || 32;
  const top = Math.max(2, Math.min(vh - hh - 2, r.top + 10));
  const left = Math.max(2, Math.min(vw - hw - 2, r.right - hw - 10));
  ov.host.style.top = top + 'px';
  ov.host.style.left = left + 'px';
}

// add badges for downloadable videos, drop them when video/stream goes away
function syncOverlays() {
  document.querySelectorAll('video').forEach((v) => {
    const dl = getVideoDownload(v);
    if (!dl) return;
    let ov = videoOverlays.get(v);
    if (!ov) {
      ov = createOverlay(v);
      videoOverlays.set(v, ov);
    }
    ov.updateState(dl);
  });
  videoOverlays.forEach((ov, v) => {
    if (!v.isConnected || !getVideoDownload(v)) {
      ov.host.remove();
      videoOverlays.delete(v);
    }
  });
}

// reposition (rAF-throttled, not a continuous loop -> no idle CPU burn)
let _rafPending = false;
function positionAll() {
  _rafPending = false;
  videoOverlays.forEach((ov, v) => positionOverlay(v, ov));
}
function scheduleReposition() {
  if (_rafPending) return;
  _rafPending = true;
  requestAnimationFrame(positionAll);
}

window.addEventListener('scroll', scheduleReposition, true);
window.addEventListener('resize', scheduleReposition, true);
document.addEventListener('fullscreenchange', () => {
  const container = document.fullscreenElement || document.body;
  videoOverlays.forEach((ov) => container.appendChild(ov.host));
  scheduleReposition();
});

// new/removed <video> elements anywhere on the page
const _videoObserver = new MutationObserver(() => { syncOverlays(); scheduleReposition(); });
try {
  _videoObserver.observe(document.documentElement, { childList: true, subtree: true });
} catch (e) { /* documentElement not ready yet; the interval below covers it */ }

function sniffRuntimeMedia() {
  if (!captureEnabled) return;

  const runtimeCandidates = [];

  const hlsUrl = window.hls?.url;
  if (hlsUrl && !hlsUrl.startsWith('blob:') && !hlsUrl.startsWith('data:')) {
    runtimeCandidates.push({
      url: hlsUrl,
      filename: hlsUrl.split('?')[0].split('/').pop() || 'playlist.m3u8',
      mime: 'application/x-mpegurl',
      size: 0,
      kind: 'hls'
    });
  }

  const levelUrls = Array.isArray(window.hls?.levels) ? window.hls.levels : [];
  levelUrls.forEach((entry) => {
    const url = Array.isArray(entry) ? entry[0] : entry?.url || entry;
    if (typeof url === 'string' && url.startsWith('http')) {
      runtimeCandidates.push({
        url,
        filename: url.split('?')[0].split('/').pop() || 'video.m3u8',
        mime: 'application/x-mpegurl',
        size: 0,
        kind: 'hls'
      });
    }
  });

  const playerSrc = window.player?.media?.src || document.querySelector('video.player')?.currentSrc;
  if (playerSrc && !playerSrc.startsWith('blob:') && !playerSrc.startsWith('data:')) {
    runtimeCandidates.push({
      url: playerSrc,
      filename: playerSrc.split('?')[0].split('/').pop() || 'video.mp4',
      mime: 'video/mp4',
      size: 0,
      kind: 'file'
    });
  }

  runtimeCandidates.forEach((candidate) => addSniffedMedia(candidate));
}

// Also scan the DOM for static media (direct <video>/<audio> src and <source>).
// Note: streaming sites use Media Source Extensions -> the src is a blob: URL
// that points at in-memory buffers, NOT a downloadable file. Those can only be
// grabbed via the HLS/DASH manifest the network sniffer catches above.
function scanDom() {
  if (!captureEnabled) return;

  // keep the per-element download badges in sync, then reposition
  try {
    syncOverlays();
    scheduleReposition();
  } catch (e) {
    console.error('HyperFetch overlay error:', e);
  }

  document.querySelectorAll('video, audio').forEach(v => {
    const urls = [];
    if (v.currentSrc) urls.push(v.currentSrc);
    if (v.src) urls.push(v.src);
    v.querySelectorAll('source').forEach(s => { if (s.src) urls.push(s.src); });
    urls.forEach(u => {
      if (!u || u.startsWith('blob:') || u.startsWith('data:')) return;
      const isHls = /\.m3u8(\?.*)?$/i.test(u);
      addSniffedMedia({
        url: u,
        filename: u.split('?')[0].split('/').pop() || 'media_file',
        mime: isHls ? 'application/x-mpegurl' : (v.tagName === 'VIDEO' ? 'video/mp4' : 'audio/mp3'),
        size: 0,
        kind: isHls ? 'hls' : 'file'
      });
    });
  });
}
setInterval(scanDom, 500);
setInterval(sniffRuntimeMedia, 500);

// Run immediately on page load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => { scanDom(); sniffRuntimeMedia(); }, 100);
  });
} else {
  setTimeout(() => { scanDom(); sniffRuntimeMedia(); }, 100);
}
