"""The source of the GPL programs a release ships, as one zip the release
workflow attaches beside the installer (.github/workflows/release.yml):

  aria2/   aria2 1.37.0 and the six libraries its Windows build is made of
           (bin/aria2c.exe is aria2's own build1, from the Dockerfile.mingw
           in its source);
  ffmpeg/  FFmpeg at the commit build/FFmpeg-version.txt names, and the
           FFmpeg-Builds scripts of the build that made that ffmpeg.exe -
           each names the repository and commit of one library in it.

    python tools/gpl_sources.py <version> <folder for the zip>

HyperFetch offered this source on request, for three years from each
release: a promise somebody had to keep (2026-11-11). Put on the page the
installer comes from, the source needs nobody (GPLv2 section 3, GPLv3
section 6(d)).
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDS = "FFmpeg-Builds/FFmpeg-Builds"     # BtbN/FFmpeg-Builds, moved in 2026
# What bin/aria2c.exe is made from: aria2's own source, and the libraries its
# Dockerfile.mingw (release-1.37.0) builds it with. Pinned: a file that is not
# the one checked stops the release.
ARIA2 = (
    ("https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0.tar.xz",
     "60a420ad7085eb616cb6e2bdf0a7206d68ff3d37fb5a956dc44242eb2f79b66b"),
    ("https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz",
     "a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898"),
    ("https://github.com/libexpat/libexpat/releases/download/R_2_5_0/expat-2.5.0.tar.bz2",
     "6f0e6e01f7b30025fa05c85fdad1e5d0ec7fd35d9f61b22f34998de11969ff67"),
    ("https://www.sqlite.org/2023/sqlite-autoconf-3430100.tar.gz",
     "39116c94e76630f22d54cd82c3cea308565f1715f716d1b2527f1c9c969ba4d9"),
    ("https://github.com/madler/zlib/releases/download/v1.3/zlib-1.3.tar.gz",
     "ff0ba4c292013dbc27530b3a81e1f9a813cd39de01ca5e0f8bf355702efa593e"),
    ("https://github.com/c-ares/c-ares/releases/download/cares-1_19_1/c-ares-1.19.1.tar.gz",
     "321700399b72ed0e037d0074c629e7741f6b2ec2dda92956abe3e9671d3e268e"),
    ("https://github.com/libssh2/libssh2/releases/download/libssh2-1.11.0/libssh2-1.11.0.tar.bz2",
     "e56d81f01f090d3c6eaf5f56c645610fd1a90d92819541d0079ae363d1eed11e"),
)


def _get(url, tries=3, delay=2.0):
    last = None
    for n in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": "HyperFetch-release"})
        token = os.environ.get("GH_TOKEN")
        if token and url.startswith("https://api.github.com/"):
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except OSError as e:
            last = e
            code = getattr(e, "code", None)
            if code and 400 <= code < 500 and code not in (408, 429):
                raise
            if n < tries - 1:
                time.sleep(delay * (n + 1))
    raise last


def ffmpeg_build(line):
    """The FFmpeg version in the line `ffmpeg -version` starts with, as
    FFmpeg-Builds names its zips: "ffmpeg version N-128790-g9bee1e603c-
    20261111 Copyright ..." -> "N-128790-g9bee1e603c"."""
    m = re.match(r"ffmpeg version (\S+-g[0-9a-f]+)-\d{8} ", line)
    if not m:
        raise SystemExit("not a line of an FFmpeg-Builds ffmpeg: %r" % line[:80])
    return m.group(1)


def builds_tag(releases, version):
    """The FFmpeg-Builds release that published this ffmpeg: its tag is the
    commit of the scripts that built it."""
    want = "ffmpeg-%s-win64-gpl.zip" % version
    for r in releases:
        if any(a.get("name") == want for a in r.get("assets") or ()):
            return r["tag_name"]
    raise SystemExit("no FFmpeg-Builds release has %s" % want)


def main(version, out):
    parts = []
    for url, sha in ARIA2:
        data = _get(url)
        if hashlib.sha256(data).hexdigest() != sha:
            raise SystemExit("%s is not the file that was checked" % url)
        parts.append(("aria2/" + url.rsplit("/", 1)[1], data))
    with open(os.path.join(ROOT, "build", "FFmpeg-version.txt"), encoding="utf-8") as f:
        line = f.readline().strip()
    ff = ffmpeg_build(line)
    commit = json.loads(_get("https://api.github.com/repos/FFmpeg/FFmpeg/commits/"
                             + ff.rsplit("-g", 1)[1]))["sha"]
    parts.append(("ffmpeg/FFmpeg-%s.tar.gz" % commit,
                  _get("https://github.com/FFmpeg/FFmpeg/archive/%s.tar.gz" % commit)))
    tag = builds_tag(json.loads(_get("https://api.github.com/repos/%s/releases?per_page=100"
                                     % BUILDS)), ff)
    parts.append(("ffmpeg/FFmpeg-Builds-%s.tar.gz" % tag,
                  _get("https://github.com/%s/archive/refs/tags/%s.tar.gz" % (BUILDS, tag))))
    readme = (
        "The source of the GPL programs HyperFetch %s ships\n"
        "(licenses\\THIRD-PARTY-NOTICES.txt in its folder says which they are).\n\n"
        "aria2/   aria2 1.37.0 - bin\\aria2c.exe is aria2's own\n"
        "         aria2-1.37.0-win-64bit-build1 - and the libraries that build is made\n"
        "         of. Dockerfile.mingw and mingw-config, in aria2's source, put them\n"
        "         together.\n"
        "ffmpeg/  FFmpeg at commit %s -\n"
        "         bin\\ffmpeg.exe is version %s - and the scripts of the\n"
        "         FFmpeg-Builds build %s that made it\n"
        "         (https://github.com/%s): each library's script in\n"
        "         scripts.d names its repository and exact commit.\n"
        % (version, commit, ff, tag, BUILDS))
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "HyperFetch-%s-gpl-source.zip" % version)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:   # tarballs: packed already
        z.writestr("README.txt", readme)
        for name, data in parts:
            z.writestr(name, data)
    print(path)
    return path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: gpl_sources.py <version> <folder for the zip>")
    main(sys.argv[1], sys.argv[2])
