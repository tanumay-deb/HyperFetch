"""What the desktop PyInstaller spec must and must not carry.

A spec fails quietly. Editing one is easy to get wrong, the build still
succeeds, and the mistake only shows up as "torrents do not work" on somebody
else's machine. A spec that stops bundling aria2c produces an app that looks
fine until the first magnet link.

The server spec is tested in its own repository now, alongside the users site.
What stays here is the desktop's, plus the guarantee that the desktop never
reaches the users site at all - which the spec alone cannot give, because
PyInstaller bundles what the code imports, not what the spec mentions.
"""
import ast
import io
import os
import re

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "HyperFetch.spec")

# The users site is the paid half and lives in a private repository. These
# names must not appear anywhere the desktop build can reach.
SITE_MODULES = ("site_server", "site_auth", "site_limits", "site_audit",
                "site_tunnel")


def _read(p):
    return io.open(p, encoding="utf-8").read()


def test_the_spec_is_valid_python():
    """A spec is executed, not parsed by a schema, so a syntax error is a
    broken release rather than a warning."""
    ast.parse(_read(DESKTOP))


def test_aria2c_is_bundled():
    """Without it every magnet and .torrent fails, and nothing else in the app
    changes — so the build looks healthy right up until somebody uses it."""
    src = _read(DESKTOP)
    assert "aria2c" in src, os.path.basename(DESKTOP)
    assert re.search(r"extra_datas\.append\(\('bin/aria2c", src), os.path.basename(DESKTOP)


def test_the_desktop_bundles_what_it_serves():
    src = _read(DESKTOP)
    assert "('web', 'web')" in src, "the control page would 404 in a frozen build"
    assert "assets/icons" in src, "the icons are loaded from disk at runtime"
    assert "ffmpeg" in src, "yt-dlp merges need it for 1080p and DASH"


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


def test_lazily_imported_engine_modules_are_declared():
    """These are imported inside functions, so PyInstaller cannot see them and
    the frozen build fails at the moment somebody uses that feature."""
    src = _read(DESKTOP)
    for mod in ("hls", "torrent", "yt_dl", "doh", "upnp"):
        assert "'%s'" % mod in src, "%s: %s" % (os.path.basename(DESKTOP), mod)
