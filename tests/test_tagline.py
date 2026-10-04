"""The brand line is said the same way everywhere it is said.

"Fetch everything. Download faster." - chosen 2026-10-03. The words live in
utils.TAGLINE. The app's About page shows that; the site, the README and the
extension's welcome page carry the same words, because none of them can
import them. So a change of wording is made in utils, and these tests say
where else it has to follow.

Not checked here: the extension's manifest description and the store
listings. They describe the extension rather than the app.
"""
import os
import re

import utils

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_readme_opens_with_it():
    lines = [line.strip() for line in _read("README.md").splitlines() if line.strip()]
    assert lines[0] == "# HyperFetch"
    assert lines[1] == "**%s**" % utils.TAGLINE, "the line under the title is not the brand line"


def test_the_site_says_it_above_its_headline():
    page = _read("docs", "index.html")
    header = re.search(r"<header>(.*?)</header>", page, re.S).group(1)
    said = header.find('<p class="tagline">%s</p>' % utils.TAGLINE)
    assert said >= 0, "the site's header does not carry the brand line"
    assert said < header.find("<h1"), "the brand line belongs above the headline"


def test_a_shared_link_to_the_site_shows_it():
    """og:title is what a chat or a feed prints for the link. The page's own
    <title> keeps the words people search for."""
    page = _read("docs", "index.html")
    og = re.search(r'<meta property="og:title" content="([^"]*)">', page).group(1)
    assert og == "HyperFetch — %s" % utils.TAGLINE
    title = re.search(r"<title>(.*?)</title>", page).group(1)
    assert "download accelerator" in title


def test_the_extensions_welcome_page_says_it():
    """The page a browser opens when the extension is installed."""
    page = _read("chrome_ext", "welcome.html")
    assert '<span class="tagline">%s</span>' % utils.TAGLINE in page


def test_the_about_page_shows_it():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    dlg = SettingsDialogV2(None, extras={}, save_dir=".", max_concurrent=3, segments=8,
                           verify_tls=True, pair_token="T", theme="Dark", accent="purple",
                           sched_en=False, sched_start="00:00", sched_stop="06:00")
    try:
        shown = [label.text() for label in dlg.findChildren(QLabel)]
        assert utils.TAGLINE in shown
        assert "A modern, fast and reliable download manager." not in shown
    finally:
        dlg.deleteLater()
