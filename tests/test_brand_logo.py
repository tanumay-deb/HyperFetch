"""One logo, kept in one place.

"in privacy.html and the html page we get after installing the extension have
a diff logo of hyperfetch" (2026-10-04). The privacy page, the uninstall page,
the extension's welcome page and its popup each drew their own: a purple tile
with the lightning-bolt EMOJI on it. An emoji is drawn by whatever system shows
it - yellow and orange on Windows - so those four never showed the logo.

assets/brand/logo.svg is the logo. Every other copy is that drawing at a size:
the app's icon, the site's, the extension's three. Pages show one of those
files and draw nothing of their own. The site serves the master as logo.svg,
so there is one address to fetch it from.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(ROOT, "assets", "brand", "logo.svg")

# every raster copy of the logo, and what it is for
COPIES = ["assets/icon.png", "docs/logo.png"] + [
    "%s_ext/icons/icon%d.png" % (browser, size)
    for browser in ("chrome", "edge", "firefox") for size in (16, 48, 128)]


def _read(*parts, mode="r"):
    kw = {} if "b" in mode else {"encoding": "utf-8"}
    with open(os.path.join(ROOT, *parts), mode, **kw) as f:
        return f.read()


@pytest.fixture(scope="module")
def qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def _drawn(size):
    """The master, drawn at size x size."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(MASTER)
    assert renderer.isValid(), "assets/brand/logo.svg does not parse"
    img = QImage(size, size, QImage.Format_RGBA8888_Premultiplied)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return img


def _apart(a, b):
    """How far two pictures are apart: (mean difference per channel, share of
    pixels off by more than 24 of 255 in some channel), alpha premultiplied."""
    from PySide6.QtGui import QImage
    fmt = QImage.Format_RGBA8888_Premultiplied
    a, b = a.convertToFormat(fmt), b.convertToFormat(fmt)
    assert (a.width(), a.height()) == (b.width(), b.height())
    xa, xb = bytes(a.constBits()), bytes(b.constBits())
    total = far = 0
    for i in range(0, len(xa), 4):
        d = [abs(xa[i + k] - xb[i + k]) for k in range(4)]
        total += sum(d)
        far += max(d) > 24
    return total / len(xa), far / (len(xa) / 4)


@pytest.mark.parametrize("copy", COPIES)
def test_every_copy_is_the_master_at_its_size(qt, copy):
    """Measured when the master was traced from assets/icon.png: mean 0.04 to
    0.08 of 255 at every size, no pixel far off. Another drawing - a redrawn
    bolt, other colours, a different corner - is off by whole units."""
    from PySide6.QtGui import QImage
    png = QImage(os.path.join(ROOT, *copy.split("/")))
    assert not png.isNull(), copy + " is missing or unreadable"
    assert png.width() == png.height()
    mean, far = _apart(_drawn(png.width()), png)
    assert mean < 0.5 and far < 0.002, "%s is not the logo: mean %.3f, far %.4f" % (copy, mean, far)


def test_the_site_serves_the_master_itself():
    """https://tanumay-deb.github.io/HyperFetch/logo.svg is where to fetch it."""
    master = _read("assets", "brand", "logo.svg", mode="rb").replace(b"\r\n", b"\n")
    served = _read("docs", "logo.svg", mode="rb").replace(b"\r\n", b"\n")
    assert served == master
    assert _read("docs", "logo.png", mode="rb") == _read("assets", "icon.png", mode="rb")


@pytest.mark.parametrize("page,image", [
    ("docs/privacy.html", "logo.png"),
    ("docs/uninstall.html", "logo.png"),
    ("docs/index.html", "logo.png"),
    ("chrome_ext/welcome.html", "icons/icon128.png"),
    ("chrome_ext/popup.html", "icons/icon48.png"),
])
def test_a_page_shows_the_logo_file(page, image):
    html = _read(*page.split("/"))
    assert re.search(r'<img\b[^>]*\bsrc="%s"' % re.escape(image), html), (
        "%s does not show %s" % (page, image))


@pytest.mark.parametrize("path", [
    "docs/privacy.html", "docs/uninstall.html", "docs/index.html",
    "chrome_ext/welcome.html", "chrome_ext/welcome.js", "chrome_ext/popup.html",
    "chrome_ext/popup.js", "chrome_ext/background.js", "chrome_ext/content.js",
])
def test_nothing_stands_an_emoji_in_for_the_logo(path):
    """Not as a tile, and not in a sentence ("click the [bolt] icon"): the icon
    in the toolbar is the logo, and the emoji is not it."""
    assert "⚡" not in _read(*path.split("/")), path + " uses the bolt emoji"
