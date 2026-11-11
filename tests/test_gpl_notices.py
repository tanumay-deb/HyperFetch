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
    ffmpeg = _read("licenses", "FFmpeg-COPYING.GPLv3.txt")
    assert "GNU GENERAL PUBLIC LICENSE" in aria2[:200] and "Version 2, June 1991" in aria2[:200]
    assert "GNU GENERAL PUBLIC LICENSE" in ffmpeg[:200] and "Version 3, 29 June 2007" in ffmpeg[:200]
    assert "END OF TERMS AND CONDITIONS" in aria2 and "END OF TERMS AND CONDITIONS" in ffmpeg


def test_the_notice_says_what_is_shipped_and_where_its_source_is():
    notice = _read("licenses", "THIRD-PARTY-NOTICES.txt")
    aria2_version = re.search(r'\$ariaVersion\s*=\s*"([\d.]+)"', _read("build.ps1")).group(1)
    assert ("aria2 %s" % aria2_version) in notice, "the notice names another aria2 than the build's"
    assert ("release-%s/aria2-%s.tar.xz" % (aria2_version, aria2_version)) in notice
    assert "https://github.com/BtbN/FFmpeg-Builds" in notice
    assert "https://github.com/FFmpeg/FFmpeg" in notice and "FFmpeg-version.txt" in notice
    for name in ("aria2-COPYING.txt", "FFmpeg-COPYING.GPLv3.txt"):
        assert name in notice and os.path.isfile(os.path.join(LICENSES, name)), name
    assert "three years" in notice and "/issues" in notice, "no written offer of the source"


def test_the_build_ships_them_and_which_ffmpeg_it_is():
    spec = _read("HyperFetch.spec")
    assert "('licenses', 'licenses')" in spec
    assert "FFmpeg-version.txt" in spec and "-version" in spec


def test_credits_points_at_the_notice():
    import inspect
    from gui2.dialogs import settings_pages
    assert "THIRD-PARTY-NOTICES.txt" in inspect.getsource(
        settings_pages.PageBuilderMixin._show_credits)
