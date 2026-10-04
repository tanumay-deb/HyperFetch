"""Each browser is pointed at its own store.

The extension is in three stores: Chrome's, Edge's, and - approved 2026-10-04 -
Firefox's (addons.mozilla.org/addon/hyperfetch). The site, the README and the
app named only Chrome's, so a Firefox user was sent to a store Firefox cannot
install from.

The addresses live in utils.EXTENSION_STORES. The app's "Get Extension" offers
each; the site and the README repeat them by hand, and these tests say where
they have to follow.
"""
import os
import re

import pytest

import utils

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_there_are_three_stores():
    assert list(utils.EXTENSION_STORES) == ["Chrome Web Store", "Firefox Add-ons", "Edge Add-ons"]
    assert utils.EXTENSION_STORES["Firefox Add-ons"] == "https://addons.mozilla.org/addon/hyperfetch/"
    # the listings' ids are the ones /pair answers (api_server.TRUSTED_EXT_IDS)
    import api_server
    for store in ("Chrome Web Store", "Edge Add-ons"):
        listing = utils.EXTENSION_STORES[store].rstrip("/").rsplit("/", 1)[1]
        assert listing in api_server.TRUSTED_EXT_IDS, store


@pytest.mark.parametrize("page", ["docs/index.html", "README.md"])
@pytest.mark.parametrize("store", ["Chrome Web Store", "Firefox Add-ons", "Edge Add-ons"])
def test_the_site_and_the_readme_link_every_store(page, store):
    assert utils.EXTENSION_STORES[store] in _read(*page.split("/")), (
        "%s does not link %s" % (page, store))


@pytest.mark.parametrize("page,link_id", [("docs/index.html", "getExtension"),
                                          ("docs/uninstall.html", "reinstall")])
def test_a_page_sends_each_browser_to_its_own_store(page, link_id):
    """One button, the visitor's own store behind it: Chrome's as written, and
    a script that swaps in Firefox's or Edge's when that is the browser."""
    html = _read(*page.split("/"))
    button = re.search(r'<a\b[^>]*\bid="%s"[^>]*>' % link_id, html)
    assert button, "%s has no #%s" % (page, link_id)
    assert utils.EXTENSION_STORES["Chrome Web Store"] in button.group(0)
    script = html[html.index("<script"):]
    assert link_id in script
    for store in ("Firefox Add-ons", "Edge Add-ons"):
        assert utils.EXTENSION_STORES[store] in script, "%s: no swap to %s" % (page, store)


def test_the_app_offers_each_store():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    dlg = SettingsDialogV2(None, extras={}, save_dir=".", max_concurrent=3, segments=8,
                           verify_tls=True, pair_token="T", theme="Dark", accent="purple",
                           sched_en=False, sched_start="00:00", sched_stop="06:00")
    try:
        offered = [a.text() for a in dlg.get_extension.menu().actions()]
        assert offered == list(utils.EXTENSION_STORES)
    finally:
        dlg.deleteLater()


def test_the_first_run_welcome_names_each_store():
    from gui2.dialogs import welcome
    text = " ".join(welcome._STEPS)
    for store, url in utils.EXTENSION_STORES.items():
        assert url in text and store in text, store
