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
    for perm in ("downloads", "storage"):
        assert perm in m["permissions"]
    assert m["background"]["service_worker"] == "background.js"
    assert m["action"]["default_popup"] == "popup.html"


def test_icons_exist():
    for size in (16, 48, 128):
        assert os.path.exists(os.path.join(EXT, "icons", f"icon{size}.png"))


def test_file_re_in_sync():
    """background.js and content.js must share the identical FILE_RE literal."""
    def grab(src):
        m = re.search(r"const FILE_RE\s*=\s*(/.*?/i);", src)
        return m.group(1) if m else None
    bg = grab(_read("background.js"))
    ct = grab(_read("content.js"))
    assert bg and ct and bg == ct


def test_popup_ids_wired():
    html = _read("popup.html")
    js = _read("popup.js")
    for el_id in re.findall(r'getElementById\("(\w+)"\)', js):
        assert f'id="{el_id}"' in html, f"popup.js references #{el_id} not in popup.html"


def test_background_sends_token_header():
    bg = _read("background.js")
    assert "X-SDM-Token" in bg
    assert "chrome.cookies.getAll" in bg   # cookie forwarding present


def test_sniffer_message_type_matches():
    bg = _read("background.js")
    ct = _read("content.js")
    assert "SNIFFED_MEDIA" in bg and "SNIFFED_MEDIA" in ct


def test_no_hardcoded_verify_false_in_python():
    """Ensure TLS verification isn't silently disabled anywhere in the app."""
    root = os.path.dirname(EXT)
    for fn in ("downloader.py", "hls.py"):
        src = open(os.path.join(root, fn), encoding="utf-8").read()
        assert "verify=False" not in src
