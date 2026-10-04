"""How fast one video comes down two ways: yt-dlp's own download, and the
app's segmented engine given the format yt-dlp chose.

    python tools/bench_direct_video.py URL [--runs 3] [--segments 8]

Only for a page whose chosen format is ONE direct http(s) file: no merge, no
HLS or DASH fragments. That is the only case the segmented engine could take
over, so anything else is reported and not measured.

Each round extracts the page afresh (media links are often signed and
short-lived), then downloads that one link both ways, back to back. The order
alternates A-B, B-A, ... so a network that speeds up or slows down during the
session favours neither. Both files must hash the same or the round is void.

Reported per round and as medians, in Mbit/s:
  download          the transfer alone (for the app's engine that includes its
                    probe and the move out of the temp folder, as in the app)
  with extraction   the same plus the one extraction both ways need first

It imports the app's engine and changes nothing in the app. The provider-block
fallback (doh.py) is on, as it is in the app by default; --no-dns-fallback
turns it off.
"""
import argparse
import hashlib
import logging
import os
import shutil
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import downloader       # noqa: E402
import task as T        # noqa: E402
import yt_dl            # noqa: E402


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): print("  yt-dlp:", m, file=sys.stderr)


class _Collect(logging.Handler):
    """The engine's warnings (429s, refusals) for the round's report."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _mbit(nbytes, secs):
    return nbytes * 8 / 1e6 / secs if secs > 0 else 0.0


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _extract(yt_dlp, url, fmt, headers):
    ydl = yt_dlp.YoutubeDL({
        "quiet": True, "no_warnings": True, "noprogress": True,
        "noplaylist": True, "format": fmt, "retries": 5,
        "logger": _Quiet(), "http_headers": headers,
    })
    t0 = time.perf_counter()
    info = ydl.extract_info(url, download=False)
    return ydl, info, time.perf_counter() - t0


def _by_ytdlp(ydl, info, path):
    """yt-dlp's own downloader on the chosen format (HttpFD: one connection)."""
    t0 = time.perf_counter()
    ydl.dl(path, info)
    return time.perf_counter() - t0


def _by_segments(ydl, info, page, path, segments, collect):
    """The app's engine on the same link, handed over as the app hands it:
    the headers yt-dlp would send there, yt-dlp's cookie jar, and the ramp a
    video's link gets (Downloader.run_media)."""
    t = T.DownloadTask(page, path)
    eng = downloader.Downloader(t, segments=segments, url=info["url"],
                                headers=yt_dl.media_headers(info), cookies=ydl.cookiejar)
    collect.lines.clear()
    t0 = time.perf_counter()
    started = eng.run_media()
    dt = time.perf_counter() - t0
    if not started:
        raise RuntimeError("the link would not be fetched in ranges; the app would "
                           "leave this one to yt-dlp")
    if not eng.media_done:
        raise RuntimeError("segmented download ended %s: %s" % (t.status, t.error))
    return dt, t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url", help="the video page (or a direct media link)")
    ap.add_argument("--runs", type=int, default=3, help="rounds; each downloads both ways")
    ap.add_argument("--segments", type=int, default=downloader.DEFAULT_SEGMENTS,
                    help="the app's connections per download (default %(default)s)")
    ap.add_argument("--format", default="b",
                    help="yt-dlp format selector (default: best single file)")
    ap.add_argument("--min-mb", type=float, default=50,
                    help="refuse a smaller file; small files say little")
    ap.add_argument("--header", action="append", default=[],
                    help='extra request header for the page, "Name: value" (repeatable)')
    ap.add_argument("--dir", help="where to write (default: a temp folder, removed after)")
    ap.add_argument("--no-dns-fallback", action="store_true",
                    help="do not use the app's provider-block fallback")
    a = ap.parse_args(argv)

    try:
        import yt_dlp
    except ImportError:
        print("yt-dlp is not installed: pip install -r requirements.txt")
        return 2
    if not a.no_dns_fallback:
        import doh
        doh.set_auto(True)
    collect = _Collect()
    logging.getLogger("hyperfetch").addHandler(collect)
    logging.getLogger("hyperfetch").setLevel(logging.WARNING)

    headers = {}
    for h in a.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()

    out = a.dir or tempfile.mkdtemp(prefix="hf-bench-")
    os.makedirs(out, exist_ok=True)
    print("yt-dlp %s, %d round(s), %d connection(s) for the app's engine, files in %s"
          % (yt_dlp.version.__version__, a.runs, a.segments, out))
    rows = []
    try:
        for r in range(a.runs):
            ydl, info, t_ex = _extract(yt_dlp, a.url, a.format, headers)
            if not yt_dl.direct_format(info):
                print("The chosen format (%s, protocol %s%s) is not one plain file, so "
                      "the app leaves it to yt-dlp. Nothing to measure."
                      % (info.get("format_id"), info.get("protocol"),
                         ", a merge" if info.get("requested_formats") else ""))
                return 1
            size = info.get("filesize") or info.get("filesize_approx") or 0
            host = info["url"].split("/")[2]
            if r == 0:
                print("format %s, %s, %s on %s"
                      % (info.get("format_id"), info.get("ext"),
                         "%.1f MB" % (size / 1e6) if size else "size unknown", host))
            order = ("ytdlp", "app") if r % 2 == 0 else ("app", "ytdlp")
            times, paths, t_app = {}, {}, None
            for way in order:
                paths[way] = os.path.join(out, "r%d-%s.%s" % (r + 1, way, info.get("ext") or "bin"))
                if way == "ytdlp":
                    times[way] = _by_ytdlp(ydl, info, paths[way])
                else:
                    times[way], t_app = _by_segments(ydl, info, a.url, paths[way],
                                                     a.segments, collect)
            got = os.path.getsize(paths["ytdlp"])
            if got < a.min_mb * 1e6:
                print("The file is %.1f MB; at least %g MB is needed (--min-mb)."
                      % (got / 1e6, a.min_mb))
                return 1
            same = (got == os.path.getsize(paths["app"])
                    and _sha256(paths["ytdlp"]) == _sha256(paths["app"]))
            row = {
                "bytes": got, "extract": t_ex, "same": same,
                "ytdlp": _mbit(got, times["ytdlp"]), "app": _mbit(got, times["app"]),
                "ytdlp_all": _mbit(got, t_ex + times["ytdlp"]),
                "app_all": _mbit(got, t_ex + times["app"]),
                "segs": len(t_app.segments), "range": t_app.supports_range,
                "warn": list(collect.lines),
            }
            rows.append(row)
            print("round %d (%s first): %.1f MB, extraction %.1fs | yt-dlp %.1f Mbit/s "
                  "(%.1fs) | app %.1f Mbit/s (%.1fs, %d segment(s)%s) | %s"
                  % (r + 1, order[0], got / 1e6, t_ex, row["ytdlp"], times["ytdlp"],
                     row["app"], times["app"], row["segs"],
                     "" if row["range"] else ", no ranges",
                     "same bytes" if same else "FILES DIFFER - round void"))
            for w in row["warn"]:
                print("    engine:", w)
            for p in paths.values():
                os.remove(p)
    finally:
        if not a.dir:
            shutil.rmtree(out, ignore_errors=True)

    good = [x for x in rows if x["same"]]
    if not good:
        print("No round produced the same file both ways.")
        return 1
    med = {k: statistics.median(x[k] for x in good)
           for k in ("ytdlp", "app", "ytdlp_all", "app_all")}
    print("median of %d round(s): download yt-dlp %.1f vs app %.1f Mbit/s (x%.2f); "
          "with extraction %.1f vs %.1f Mbit/s (x%.2f)"
          % (len(good), med["ytdlp"], med["app"], med["app"] / med["ytdlp"],
             med["ytdlp_all"], med["app_all"], med["app_all"] / med["ytdlp_all"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
