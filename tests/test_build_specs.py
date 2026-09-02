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


# The users site is the paid half. These two tests are the difference between
# believing the desktop excludes it and knowing.
SITE_MODULES = ("site_server", "site_auth", "site_limits", "site_audit",
                "site_tunnel")


def test_the_desktop_spec_does_not_name_the_users_site():
    """Necessary, and — on its own — nowhere near sufficient. This assertion
    passed for the whole time the shipped binary contained the users site,
    because PyInstaller does not need the spec's permission: it follows
    imports, and one `import site_limits` nested inside a function in
    queue_manager was enough. See the build test below, which is the one that
    would have caught it."""
    src = _read(DESKTOP)
    assert "('site', 'site')" not in src
    for mod in SITE_MODULES:
        assert mod not in src, mod


def test_nothing_in_the_desktop_graph_imports_the_users_site():
    """What the spec test above cannot see: the actual import graph.

    PyInstaller bundles what it can reach. So the guarantee has to be that the
    desktop's modules never reach the site at all — including from inside a
    function body, which is exactly where the import that broke this hid.
    """
    import ast as _ast

    seen, stack, edges = set(), ["main"], []
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = os.path.join(ROOT, mod + ".py")
        paths = [path] if os.path.exists(path) else []
        pkg = os.path.join(ROOT, mod)
        if os.path.isdir(pkg):
            for r, _d, fs in os.walk(pkg):
                if "__pycache__" in r:
                    continue
                paths += [os.path.join(r, f) for f in fs if f.endswith(".py")]
        for p in paths:
            try:
                tree = _ast.parse(io.open(p, encoding="utf-8").read())
            except (OSError, SyntaxError):
                continue
            for node in _ast.walk(tree):
                names = []
                if isinstance(node, _ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, _ast.ImportFrom) and node.module and not node.level:
                    names = [node.module.split(".")[0]]
                for n in names:
                    if n in SITE_MODULES:
                        edges.append("%s -> %s" % (os.path.relpath(p, ROOT), n))
                    elif (os.path.exists(os.path.join(ROOT, n + ".py"))
                          or os.path.isdir(os.path.join(ROOT, n))):
                        stack.append(n)

    assert not edges, (
        "the desktop reaches the users site, so PyInstaller will bundle it: "
        + "; ".join(edges))


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
