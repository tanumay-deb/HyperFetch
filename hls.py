"""Minimal HLS (.m3u8) video grabber.

Web video is usually delivered as an HLS playlist: a small text manifest that
lists many short .ts/.m4s segments. Downloading the .m3u8 itself just saves the
text, not the video — you have to fetch every segment and join them. This module
does that: master->variant selection, AES-128 decryption, raw concat into one
.ts file (plays in VLC / most players without ffmpeg), with pause/cancel/progress.
"""
import os
import re
import time
import shutil
import tempfile
import struct
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

import task as T
import utils

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {"User-Agent": "Mozilla/5.0 (HyperFetch)"}
TIMEOUT = 20
SEG_RETRIES = 3
PARALLEL = 6          # concurrent segment downloads


def is_hls(url="", filename="", ctype=""):
    u = (url or "").split("?")[0].lower()
    f = (filename or "").lower()
    c = (ctype or "").lower()
    return (u.endswith(".m3u8") or f.endswith(".m3u8")
            or "mpegurl" in c)


def probe_variants(url, headers=None):
    """Fetch an HLS master and return its quality variants, best first:
    ``[{label, height, bandwidth, url, size}]``. Returns ``[]`` for a
    single-quality media playlist (nothing to choose) or on any fetch error.

    Runs in the app, so it has the real Referer/cookies/UA and no CORS — it
    works on the referer/auth-gated CDNs the browser extension's own fetch
    can't read. Backs the extension's /probe endpoint."""
    base = {**HEADERS, **(headers or {})}
    sess = requests.Session()
    try:
        text = _get(sess, url, base).text
    except requests.RequestException:
        return []
    if "#EXT-X-STREAM-INF" not in text:
        return []                       # media playlist — single quality

    lines = text.splitlines()
    variants = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        attrs = line.split(":", 1)[-1]
        # anchor to a delimiter so AVERAGE-BANDWIDTH= can't shadow BANDWIDTH=
        bw = re.search(r"(?:^|,)\s*BANDWIDTH=(\d+)", attrs, re.I)
        res = re.search(r"(?:^|,)\s*RESOLUTION=\d+x(\d+)", attrs, re.I)
        uri = ""
        for j in range(i + 1, len(lines)):
            cand = lines[j].strip()
            if cand and not cand.startswith("#"):
                uri = cand
                break
        if not uri:
            continue
        variants.append({
            "height": int(res.group(1)) if res else 0,
            "bandwidth": int(bw.group(1)) if bw else 0,
            "url": urllib.parse.urljoin(url, uri),
        })
    variants.sort(key=lambda v: (v["height"], v["bandwidth"]), reverse=True)

    # estimate sizes from the top variant's total duration (one extra fetch)
    duration = 0.0
    if variants:
        try:
            vtext = _get(sess, variants[0]["url"], base).text
            for m in re.finditer(r"#EXTINF:\s*([\d.]+)", vtext):
                duration += float(m.group(1) or 0)
        except requests.RequestException:
            duration = 0.0

    out = []
    for v in variants:
        if v["height"]:
            label = f"{v['height']}p"
        elif v["bandwidth"]:
            label = f"{round(v['bandwidth'] / 1000)} kbps"
        else:
            label = "variant"
        size = int(v["bandwidth"] / 8 * duration) if (duration and v["bandwidth"]) else 0
        out.append({"label": label, "height": v["height"],
                    "bandwidth": v["bandwidth"], "url": v["url"], "size": size})
    return out


def _get(session, url, headers, **kw):
    last = None
    for _ in range(SEG_RETRIES):
        try:
            r = session.get(url, headers=headers, timeout=TIMEOUT,
                            verify=utils.VERIFY_TLS, allow_redirects=True, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(1)
    raise last


class HlsDownloader:
    def __init__(self, dtask: "T.DownloadTask"):
        self.t = dtask
        self.headers = {**HEADERS, **(getattr(dtask, "headers", None) or {})}
        self.session = requests.Session()       # used for playlist/key fetches
        self._tls = threading.local()            # per-thread session for segments

    def _seg_session(self):
        s = getattr(self._tls, "s", None)
        if s is None:
            s = requests.Session()
            self._tls.s = s
        return s

    # ----------------------------------------------------------- playlist parse
    def _fetch_text(self, url):
        return _get(self.session, url, self.headers).text

    def _is_master(self, text):
        return "#EXT-X-STREAM-INF" in text

    def _pick_variant(self, text, base_url):
        """From a master playlist choose the highest-bandwidth variant URL."""
        best_bw, best_url = -1, None
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                bw = 0
                for attr in line.split(":", 1)[-1].split(","):
                    if attr.strip().upper().startswith("BANDWIDTH="):
                        try:
                            bw = int(attr.split("=", 1)[1])
                        except ValueError:
                            bw = 0
                # the next non-comment line is the variant URI
                for j in range(i + 1, len(lines)):
                    cand = lines[j].strip()
                    if cand and not cand.startswith("#"):
                        if bw > best_bw:
                            best_bw, best_url = bw, urllib.parse.urljoin(base_url, cand)
                        break
        return best_url

    def _parse_media(self, text, base_url):
        """Return (segments, endlist). segments = [(url, seq, key)].
        endlist = True only if the playlist contains #EXT-X-ENDLIST — i.e. it is
        finite VOD. Sliding-window live/event playlists return endlist=False so
        the caller can refuse a partial-byte resume (their segment URIs change
        between fetches even when the segment count doesn't, which would silently
        concatenate bytes from a different point in the stream)."""
        segments = []
        key = None  # {"method","uri","iv"}
        seq = 0
        endlist = False
        for line in text.splitlines():
            line = line.strip()
            if line == "#EXT-X-ENDLIST":
                endlist = True
            elif line.startswith("#EXT-X-MEDIA-SEQUENCE"):
                try:
                    seq = int(line.split(":", 1)[1])
                except ValueError:
                    seq = 0
            elif line.startswith("#EXT-X-KEY"):
                key = self._parse_key(line, base_url)
            elif line and not line.startswith("#"):
                segments.append((urllib.parse.urljoin(base_url, line), seq, key))
                seq += 1
        return segments, endlist

    def _parse_key(self, line, base_url):
        attrs = {}
        body = line.split(":", 1)[1]
        # split on commas not inside quotes
        for part in self._split_attrs(body):
            if "=" in part:
                k, v = part.split("=", 1)
                attrs[k.strip().upper()] = v.strip().strip('"')
        method = attrs.get("METHOD", "NONE")
        if method == "NONE":
            return None
        uri = urllib.parse.urljoin(base_url, attrs.get("URI", ""))
        iv = attrs.get("IV")
        return {"method": method, "uri": uri, "iv": iv, "keybytes": None}

    @staticmethod
    def _split_attrs(s):
        out, cur, q = [], "", False
        for ch in s:
            if ch == '"':
                q = not q
            if ch == "," and not q:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    # ----------------------------------------------------------- decryption
    def _decrypt(self, data, key, seq):
        if not key or key["method"] != "AES-128":
            if key and key["method"] not in ("NONE", "AES-128"):
                raise RuntimeError(f"unsupported HLS encryption: {key['method']}")
            return data
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        if key["keybytes"] is None:
            key["keybytes"] = _get(self.session, key["uri"], self.headers).content
        if key.get("iv"):
            iv_hex = key["iv"][2:] if key["iv"].lower().startswith("0x") else key["iv"]
            iv = bytes.fromhex(iv_hex.rjust(32, "0"))
        else:
            iv = struct.pack(">QQ", 0, seq)  # default IV = media sequence number
        dec = Cipher(algorithms.AES(key["keybytes"]), modes.CBC(iv)).decryptor()
        out = dec.update(data) + dec.finalize()
        # strip PKCS7 padding
        if out:
            pad = out[-1]
            if 0 < pad <= 16:
                out = out[:-pad]
        return out

    # ----------------------------------------------------------- segments
    def _prefetch_keys(self, segments):
        """Resolve every distinct AES key once, before parallel downloading."""
        seen = set()
        for _, _, key in segments:
            if not key or key["method"] != "AES-128":
                if key and key["method"] not in ("NONE", "AES-128"):
                    raise RuntimeError(f"unsupported HLS encryption: {key['method']}")
                continue
            if id(key) in seen:
                continue
            seen.add(id(key))
            if key.get("keybytes") is None:
                key["keybytes"] = _get(self.session, key["uri"], self.headers).content

    def _fetch_segment(self, url, seq, key):
        """Download + decrypt one segment (runs on a worker thread)."""
        data = _get(self._seg_session(), url, self.headers).content
        return self._decrypt(data, key, seq)

    # ----------------------------------------------------------- run
    def run(self):
        self.t.status = T.DOWNLOADING
        self.t.error = ""
        self.t.supports_range = False

        # output as .ts (concatenated transport stream)
        if self.t.save_path.lower().endswith(".m3u8"):
            self.t.save_path = self.t.save_path[:-5] + ".ts"
            self.t.filename = os.path.basename(self.t.save_path)
        os.makedirs(os.path.dirname(self.t.save_path) or ".", exist_ok=True)
        temp_path = os.path.join(tempfile.gettempdir(), f"{self.t.id}.hfdownload")

        try:
            text = self._fetch_text(self.t.url)
            base = self.t.url
            if self._is_master(text):
                variant = self._pick_variant(text, base)
                if not variant:
                    raise RuntimeError("no playable variant in master playlist")
                base = variant
                text = self._fetch_text(variant)
            segments, endlist = self._parse_media(text, base)
        except (requests.RequestException, RuntimeError) as e:
            self.t.status = T.ERROR
            self.t.error = f"HLS parse failed: {e}"
            return

        if not segments:
            self.t.status = T.ERROR
            self.t.error = "HLS playlist had no segments (live stream or DRM?)"
            return

        total = len(segments)

        # ---- resume: skip segments already in the .hfdownload file ----
        # Gates: (1) finite VOD (#EXT-X-ENDLIST) — a sliding-window live/event
        # playlist republishes the same segment COUNT but different content
        # (shifted MEDIA-SEQUENCE), so appending would silently corrupt the
        # output. (2) seg_total matches. (3) the file is at least as big as the
        # saved byte count — equality would race with the unlocked two-store
        # write order (seg_done then downloaded); >= is safe because we truncate
        # the temp file back to `self.t.downloaded` before reopening in append,
        # so any extra bytes from a partial trailing segment get re-fetched.
        can_resume = (endlist and self.t.seg_total == total
                      and self.t.seg_done > 0
                      and os.path.exists(temp_path)
                      and os.path.getsize(temp_path) >= self.t.downloaded > 0)
        if can_resume:
            done = self.t.seg_done
            downloaded = self.t.downloaded
            segments = segments[done:]
            # truncate any partial trailing segment bytes so append starts clean
            with open(temp_path, "r+b") as f:
                f.truncate(downloaded)
            open_mode = "ab"
        else:
            done = 0
            downloaded = 0
            open_mode = "wb"
        self.t.seg_total = total
        self.t.seg_done = done
        self.t.downloaded = downloaded

        # Pre-fetch distinct decryption keys once (avoids a race when several
        # segment threads would otherwise fetch the same key concurrently).
        try:
            self._prefetch_keys(segments)
        except (requests.RequestException, RuntimeError) as e:
            self.t.status = T.ERROR
            self.t.error = f"HLS key fetch failed: {e}"
            return

        workers = max(1, min(PARALLEL, max(1, len(segments))))
        try:
            with open(temp_path, open_mode) as f, \
                    ThreadPoolExecutor(max_workers=workers) as ex:
                # download in ordered batches: fetch `workers` segments at once,
                # then write them to disk in playlist order. Bounds memory to one
                # batch and keeps pause/cancel latency to a single batch.
                remaining = len(segments)
                for start in range(0, remaining, workers):
                    if self.t.cancel_requested:
                        break
                    if self.t.pause_requested:
                        self.t.status = T.PAUSED
                        return
                    batch = segments[start:start + workers]
                    futures = [ex.submit(self._fetch_segment, u, seq, key)
                               for (u, seq, key) in batch]
                    for i, fut in enumerate(futures):
                        try:
                            data = fut.result()
                        except (requests.RequestException, RuntimeError) as e:
                            resp = getattr(e, "response", None)
                            if resp is not None and resp.status_code == 403:
                                self.t.status = T.ERROR
                                self.t.error = "HTTP 403 Forbidden - URL expired"
                                return
                            self.t.status = T.ERROR
                            # done+i is 0-based within remaining; +1 for human display
                            self.t.error = f"segment {done + i + 1}/{total} failed: {e}"
                            return
                        f.write(data)
                        # flush BEFORE bumping seg_done so a sudden exit can never
                        # leave seg_done > durable bytes (same lesson as the byte
                        # downloader: a counted-but-unwritten segment would make
                        # resume skip past missing data).
                        f.flush()
                        downloaded += len(data)
                        done += 1
                        # Write downloaded BEFORE seg_done so an autosave that races
                        # into the gap snapshots {seg_done=N, downloaded≥segN_end}.
                        # The reverse order produced {seg_done=N+1, downloaded=old}
                        # and the resume gate's size check threw away progress.
                        self.t.downloaded = downloaded
                        self.t.seg_done = done
                        # estimate total size from average segment so far -> live %
                        self.t.total_size = int(downloaded / done * total)
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = f"disk error: {e}"
            return

        if self.t.cancel_requested or self.t.pause_requested:
            if self.t.cancel_requested:
                self._rm(temp_path)
                self.t.status = T.CANCELLED
            else:
                self.t.status = T.PAUSED
            return

        # finalize — temp is in %TEMP% (possibly a different volume), so stage
        # into the dest dir then atomically replace. On failure mark ERROR and
        # keep the temp for retry instead of claiming COMPLETED with no file.
        try:
            staged = self.t.save_path + ".hfmove"
            shutil.move(temp_path, staged)
            os.replace(staged, self.t.save_path)
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = f"finalize failed (pick another folder, then Resume): {e}"
            try:
                if os.path.exists(self.t.save_path + ".hfmove"):
                    os.remove(self.t.save_path + ".hfmove")
            except OSError:
                pass
            return
        self.t.total_size = downloaded
        self.t.downloaded = downloaded
        self.t.status = T.COMPLETED

    @staticmethod
    def _rm(path):
        try:
            os.remove(path)
        except OSError:
            pass
