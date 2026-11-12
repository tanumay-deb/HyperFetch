"""The GPL programs HyperFetch ships come with their licences and where their
source is (2026-11-11).

The build bundles aria2 (GPL-2.0-or-later) and FFmpeg (GPL-3.0-or-later). It
shipped neither licence text nor an offer of the source, which the GPL asks of
anyone who passes the programs on.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LICENSES = os.path.join(ROOT, "licenses")


def _read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_each_licence_text_is_the_real_one():
    aria2 = _read("licenses", "aria2-COPYING.txt")
    ffmpeg = _read("licenses", "GPL-3.0-only.txt")         # FFmpeg's COPYING.GPLv3
    assert "GNU GENERAL PUBLIC LICENSE" in aria2[:200] and "Version 2, June 1991" in aria2[:200]
    assert "GNU GENERAL PUBLIC LICENSE" in ffmpeg[:200] and "Version 3, 29 June 2007" in ffmpeg[:200]
    assert "END OF TERMS AND CONDITIONS" in aria2 and "END OF TERMS AND CONDITIONS" in ffmpeg


def test_the_notice_says_what_is_shipped_and_where_its_source_is():
    notice = _read("licenses", "THIRD-PARTY-NOTICES.txt")
    aria2_version = re.search(r'\$ariaVersion\s*=\s*"([\d.]+)"', _read("build.ps1")).group(1)
    assert ("aria2 %s" % aria2_version) in notice, "the notice names another aria2 than the build's"
    assert ("release-%s/aria2-%s.tar.xz" % (aria2_version, aria2_version)) in notice
    assert "https://github.com/FFmpeg-Builds/FFmpeg-Builds" in notice
    assert "https://github.com/FFmpeg/FFmpeg" in notice and "FFmpeg-version.txt" in notice
    for name in ("aria2-COPYING.txt", "GPL-3.0-only.txt"):
        assert name in notice and os.path.isfile(os.path.join(LICENSES, name)), name
    assert "gpl-source.zip" in notice and "HyperFetch/releases" in notice, "nowhere said where the source is"


def test_the_build_ships_them_and_which_ffmpeg_it_is():
    spec = _read("HyperFetch.spec")
    assert "('licenses', 'licenses')" in spec
    assert "FFmpeg-version.txt" in spec and "-version" in spec


def test_credits_points_at_the_notice():
    import inspect
    from gui2.dialogs import settings_pages
    assert "THIRD-PARTY-NOTICES.txt" in inspect.getsource(
        settings_pages.PageBuilderMixin._show_credits)


def test_qt_goes_with_the_lgpl_and_where_its_source_is():
    """Qt's wheels carry only Qt's commercial licence; HyperFetch uses Qt
    under the LGPL-3.0 and shipped no text of it (2026-11-11)."""
    lgpl = _read("licenses", "LGPL-3.0-only.txt")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in lgpl[:200] and "Version 3, 29 June 2007" in lgpl[:200]
    assert "0. Additional Definitions." in lgpl
    version = re.search(r"^PySide6==([\d.]+)$", _read("requirements.txt"), re.M).group(1)
    notice = _read("licenses", "THIRD-PARTY-NOTICES.txt")
    assert ("Qt %s" % version) in notice and ("PySide6 %s" % version) in notice
    minor = version.rsplit(".", 1)[0]
    assert ("archive/qt/%s/%s/single/qt-everywhere-src-%s.tar.xz" % (minor, version, version)) in notice
    assert ("PySide6-%s-src/pyside-setup-everywhere-src-%s.tar.xz" % (version, version)) in notice
    assert "LGPL-3.0-only.txt" in notice and "GPL-3.0-only.txt" in notice


def test_every_release_carries_the_source_of_what_it_ships():
    """The source was offered on request for three years from each release:
    a promise somebody had to keep (2026-11-11). The release carries it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gpl_sources", os.path.join(ROOT, "tools", "gpl_sources.py"))
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    aria2_version = re.search(r'\$ariaVersion\s*=\s*"([\d.]+)"', _read("build.ps1")).group(1)
    assert gs.ARIA2[0][0].endswith("/aria2-%s.tar.xz" % aria2_version), "not the aria2 the build ships"
    assert len(gs.ARIA2) == 7, "aria2 and the six libraries its Dockerfile.mingw builds in"
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) for _, sha in gs.ARIA2)
    line = "ffmpeg version N-128790-g9bee1e603c-20261111 Copyright (c) 2000-2026 the FFmpeg developers"
    assert gs.ffmpeg_build(line) == "N-128790-g9bee1e603c"
    releases = [{"tag_name": "latest", "assets": [{"name": "ffmpeg-master-latest-win64-gpl.zip"}]},
                {"tag_name": "autobuild-2026-11-11-12-39",
                 "assets": [{"name": "ffmpeg-N-128790-g9bee1e603c-win64-gpl-shared.zip"},
                            {"name": "ffmpeg-N-128790-g9bee1e603c-win64-gpl.zip"}]}]
    assert gs.builds_tag(releases, "N-128790-g9bee1e603c") == "autobuild-2026-11-11-12-39"
    release = _read(".github", "workflows", "release.yml")
    assert "python tools/gpl_sources.py" in release and "dist/HyperFetch-*-gpl-source.zip" in release
    assert release.index("tools/gpl_sources.py") > release.index("build.ps1"),         "gathered before the build wrote FFmpeg-version.txt"
    notice = _read("licenses", "THIRD-PARTY-NOTICES.txt")
    assert "Written offer" not in notice and "three years" not in notice


def test_the_build_fetches_ffmpeg_from_where_its_source_is_looked_up():
    """FFmpeg-Builds moved from BtbN/ in 2026; build.ps1 still asked the old
    name, which only redirects. The release's source step looks there too
    (tools/gpl_sources.BUILDS)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gpl_sources", os.path.join(ROOT, "tools", "gpl_sources.py"))
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    build = _read("build.ps1")
    assert ("https://github.com/%s/releases/download/latest/" % gs.BUILDS) in build
    assert "github.com/BtbN/" not in build


def test_a_dropped_connection_while_gathering_source_is_retried(monkeypatch):
    import importlib.util
    import urllib.error
    spec = importlib.util.spec_from_file_location(
        "gpl_sources", os.path.join(ROOT, "tools", "gpl_sources.py"))
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    calls = []

    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=0):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise urllib.error.URLError("connection reset")
        return Reply(b"tarball")

    monkeypatch.setattr(gs.urllib.request, "urlopen", urlopen)
    assert gs._get("https://example.test/src.tar.xz", delay=0) == b"tarball"
    assert len(calls) == 3

    # http.client.IncompleteRead is an HTTPException, not an OSError
    import http.client
    reads = []

    class CutReply:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            reads.append(1)
            if len(reads) == 1:
                raise http.client.IncompleteRead(b"part", 10)
            return b"whole-tarball"

    monkeypatch.setattr(gs.urllib.request, "urlopen", lambda req, timeout=0: CutReply())
    assert gs._get("https://example.test/src.tar.xz", delay=0) == b"whole-tarball"
    assert len(reads) == 2


