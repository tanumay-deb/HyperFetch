// Intercept clicks on direct file links and hand them to the local app.
// Keep FILE_RE in sync with background.js.
const FILE_RE = /\.(zip|rar|7z|exe|msi|dmg|pkg|iso|img|bin|dat|deb|rpm|appimage|pdf|mp4|mkv|webm|avi|mov|mp3|m4a|flac|wav|ogg|docx|xlsx|pptx|epub|apk|tar|gz|bz2|xz|zst)(\?.*)?$/i;

let captureEnabled = true;
chrome.storage.local.get({ enabled: true }, (v) => { captureEnabled = v.enabled; });
chrome.storage.onChanged.addListener((ch) => {
  if (ch.enabled) captureEnabled = ch.enabled.newValue;
});

document.addEventListener("click", function (e) {
  if (!captureEnabled) return;

  const link = e.target.closest("a");
  if (!link || !link.href) return;

  const url = link.href;
  if (!FILE_RE.test(url)) return;

  e.preventDefault();  // stop the browser's own download
  sendToApp(url);
});

function sendToApp(url, suggestedName = null) {
  const filename = suggestedName || url.split("/").pop().split("?")[0];
  // route via the background worker so the browser's cookies for this URL
  // are attached (needed for Google Drive and other login-gated downloads)
  chrome.runtime.sendMessage({ type: "DOWNLOAD_URL", url, filename }, (res) => {
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
let panelRoot = null;
let panelContainer = null;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "SNIFFED_MEDIA") {
    addSniffedMedia(msg);
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
    panelContainer.id = "sdm-media-sniffer-root";
    
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
    const badge = media.kind === "hls"
      ? '<span style="background:#6366f1;color:#fff;border-radius:4px;padding:1px 6px;font-size:10px;margin-left:6px;">STREAM</span>'
      : '';
    const sizeTxt = media.kind === "hls" ? "Video stream" : formatSize(media.size);
    item.innerHTML = `
      <div class="filename">${media.filename}${badge}</div>
      <div class="meta">${sizeTxt} • ${media.mime || 'unknown'}</div>
      <button class="btn-download">Download with SDM</button>
    `;
    item.querySelector(".btn-download").onclick = (e) => {
      e.preventDefault();
      sendToApp(media.url, media.filename);
    };
    itemsDiv.appendChild(item);
  });
}

function addSniffedMedia(media) {
  if (sniffedMedia.has(media.url)) return;
  sniffedMedia.set(media.url, media);
  updatePanel();
}

function filenameFromUrl(url) {
  return (url || '').split('?')[0].split('/').pop() || 'media_file';
}

function getRuntimeDownloadUrl(video) {
  const current = video.currentSrc || video.src || '';
  if (current && !current.startsWith('blob:') && !current.startsWith('data:')) {
    return current;
  }

  const levelUrls = Array.isArray(window.hls?.levels) ? window.hls.levels : [];
  if (levelUrls.length) {
    const first = levelUrls[0];
    const candidate = Array.isArray(first) ? first[0] : first?.url || first;
    if (typeof candidate === 'string' && candidate.startsWith('http')) return candidate;
  }

  const playlist = window.hls?.url;
  if (typeof playlist === 'string' && playlist.startsWith('http')) return playlist;
  return '';
}

function ensureVideoOverlay(video) {
  if (!video || !(video instanceof HTMLVideoElement)) return;

  const downloadUrl = getRuntimeDownloadUrl(video);
  if (!downloadUrl || downloadUrl.startsWith('blob:') || downloadUrl.startsWith('data:')) return;

  if (video.dataset.sdmOverlayAttached === downloadUrl) return;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'SDM';
  btn.setAttribute('aria-label', 'Download with SDM');
  btn.style.cssText = [
    'position: absolute',
    'top: 8px',
    'right: 8px',
    'z-index: 2147483647',
    'border: none',
    'border-radius: 999px',
    'padding: 6px 10px',
    'background: linear-gradient(135deg, #6366f1, #8b5cf6)',
    'color: #fff',
    'font: 700 11px/1 "Segoe UI", sans-serif',
    'cursor: pointer',
    'box-shadow: 0 4px 12px rgba(0,0,0,0.35)',
    'pointer-events: auto'
  ].join('; ');

  btn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    sendToApp(downloadUrl, filenameFromUrl(downloadUrl));
  });

  if (getComputedStyle(video).position === 'static') {
    video.style.position = 'relative';
  }
  if (!video.style.display || video.style.display === 'inline') {
    video.style.display = 'block';
  }

  video.appendChild(btn);
  video.dataset.sdmOverlayAttached = downloadUrl;
}

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

  document.querySelectorAll('video').forEach((v) => {
    if (v.classList.contains('preview') || v.hidden) return;
    try {
      ensureVideoOverlay(v);
    } catch (e) {
      console.error('SDM overlay error:', e);
    }
  });

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
