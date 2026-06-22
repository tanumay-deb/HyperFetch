"""Static checks on the Chrome extension: manifest, regex sync, popup wiring."""
import os
import re
import json

import pytest

EXT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "chrome_ext")


def _read(name):
    with open(os.path.join(EXT, name), encoding="utf-8") as f:
        return f.read()


def test_manifest_valid():
    m = json.loads(_read("manifest.json"))
    assert m["manifest_version"] == 3
    # capture browser downloads (Download buttons) needs the downloads API
    for perm in ("contextMenus", "storage", "downloads"):
        assert perm in m["permissions"]
    assert m["background"]["service_worker"] == "background.js"
    assert m["action"]["default_popup"] == "popup.html"


def test_icons_exist():
    for size in (16, 48, 128):
        assert os.path.exists(os.path.join(EXT, "icons", f"icon{size}.png"))


def test_capture_is_toggle_gated():
    """Capture routes browser downloads (Download buttons) and magnet/.torrent
    clicks to the app, but is gated by the on/off toggle; the right-click menu
    stays. Already-downloaded files don't re-fire onCreated, so the old
    surprise-dialog problem doesn't return."""
    bg = _read("background.js")
    ct = _read("content.js")
    assert "chrome.downloads.onCreated" in bg                # browser-download capture
    assert "captureEnabled" in bg                            # ...gated by the toggle
    assert 'document.addEventListener("click"' in ct         # magnet/.torrent click capture
    assert "magnet:" in ct
    assert "chrome.contextMenus.create" in bg                # manual menu still registered
    assert "chrome.contextMenus.onClicked" in bg


def test_popup_ids_wired():
    html = _read("popup.html")
    js = _read("popup.js")
    for el_id in re.findall(r'getElementById\("(\w+)"\)', js):
        assert f'id="{el_id}"' in html, f"popup.js references #{el_id} not in popup.html"


def test_background_sends_token_header():
    bg = _read("background.js")
    assert "X-HyperFetch-Token" in bg
    assert "chrome.cookies.getAll" in bg   # cookie forwarding present


def test_sniffer_message_type_matches():
    bg = _read("background.js")
    ct = _read("content.js")
    assert "SNIFFED_MEDIA" in bg and "SNIFFED_MEDIA" in ct
    # HLS master/variant enumeration: producer + consumer must agree
    assert "SNIFFED_HLS_MASTER" in bg and "SNIFFED_HLS_MASTER" in ct


def test_no_hardcoded_verify_false_in_python():
    """Ensure TLS verification isn't silently disabled anywhere in the app."""
    root = os.path.dirname(EXT)
    for fn in ("downloader.py", "hls.py"):
        src = open(os.path.join(root, fn), encoding="utf-8").read()
        assert "verify=False" not in src
