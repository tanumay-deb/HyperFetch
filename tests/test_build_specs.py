"""What the two PyInstaller specs must and must not carry.

These exist because a spec fails quietly. Editing one is easy to get wrong, the
build still succeeds, and the mistake only shows up as "torrents do not work"
on somebody else's machine. A spec that stops bundling aria2c produces an app
that looks fine until the first magnet link.
"""
import ast
import io
import os
import re

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "HyperFetch.spec")
SERVER = os.path.join(ROOT, "HyperFetchServer.spec")


def _read(p):
    return io.open(p, encoding="utf-8").read()


@pytest.mark.parametrize("spec", [DESKTOP, SERVER])
def test_the_spec_is_valid_python(spec):
    """A spec is executed, not parsed by a schema, so a syntax error is a
    broken release rather than a warning."""
    ast.parse(_read(spec))


@pytest.mark.parametrize("spec", [DESKTOP, SERVER])
def test_aria2c_is_bundled(spec):
    """Without it every magnet and .torrent fails, and nothing else in the app
    changes — so the build looks healthy right up until somebody uses it."""
    src = _read(spec)
    assert "aria2c" in src, os.path.basename(spec)
    assert re.search(r"extra_datas\.append\(\('bin/aria2c", src), os.path.basename(spec)


def test_the_desktop_bundles_what_it_serves():
    src = _read(DESKTOP)
    assert "('web', 'web')" in src, "the control page would 404 in a frozen build"
    assert "assets/icons" in src, "the icons are loaded from disk at runtime"
    assert "ffmpeg" in src, "yt-dlp merges need it for 1080p and DASH"


def test_the_desktop_does_not_ship_the_users_site():
    """It has no way to serve it any more, and shipping the bundle would
    suggest otherwise."""
    src = _read(DESKTOP)
    assert "('site', 'site')" not in src
    for mod in ("site_server", "site_auth", "site_limits", "site_audit"):
        assert mod not in src, mod


def test_the_server_ships_the_site_and_its_modules():
    src = _read(SERVER)
    assert "('site', 'site')" in src, "the server would only serve a holding page"
    for mod in ("site_server", "site_auth", "site_limits", "site_audit",
                "site_tunnel", "waitress"):
        assert mod in src, mod


def test_the_server_excludes_qt_by_name():
    """The reason it is a fifth of the size. Naming the gui packages as well
    as the library means a stray `import gui2` fails the build instead of
    quietly dragging Qt back in."""
    src = _read(SERVER)
    excludes = src.split("excludes=")[1].split("]")[0]
    for name in ("PySide6", "shiboken6", "'gui'", "'gui2'"):
        assert name in excludes, name
    # And nowhere that would pull it back in. Scanning the whole file would
    # only ever match the docstring explaining the exclusion.
    hidden = src.split("hidden = (")[1].split(")" + chr(10))[0]
    assert "PySide6" not in hidden, "Qt is in hiddenimports"
    assert "gui2" not in hidden, "the gui package is in hiddenimports"


def test_the_two_specs_have_different_entry_points():
    assert "['main.py']" in _read(DESKTOP)
    assert "['server.py']" in _read(SERVER)


def test_ffmpeg_is_opt_in_for_the_server():
    """138 MB that only merges yt-dlp streams. On a server it is usually a
    package manager away."""
    src = _read(SERVER)
    assert "HYPERFETCH_SERVER_FFMPEG" in src


@pytest.mark.parametrize("spec", [DESKTOP, SERVER])
def test_lazily_imported_engine_modules_are_declared(spec):
    """These are imported inside functions, so PyInstaller cannot see them and
    the frozen build fails at the moment somebody uses that feature."""
    src = _read(spec)
    for mod in ("hls", "torrent", "yt_dl", "doh", "upnp"):
        assert "'%s'" % mod in src, "%s: %s" % (os.path.basename(spec), mod)
